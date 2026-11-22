from uuid import uuid4

import pytest
from xmux_control.db.ledger import Ledger
from xmux_control.dispatch import Dispatcher, RemoteUnavailable, utcnow


class MockAgent:
    def __init__(self, lose_response=False):
        self.jobs = {}
        self.calls = 0
        self.lose_response = lose_response

    def submit(self, payload):
        self.calls += 1
        attempt = payload['attempt']
        ref = {'schema_version': '1', 'task_id': attempt['task_id'], 'agent_id': attempt['agent_id'],
               'correlation_id': attempt['correlation_id'], 'remote_task_id': str(uuid4()),
               'accepted_at': utcnow()}
        self.jobs[attempt['correlation_id']] = ref
        if self.lose_response:
            raise RemoteUnavailable('accepted_but_response_lost')
        return ref

    def reconcile(self, correlation):
        return {'schema_version': '1', 'checked_at': utcnow(),
                **{key: self.jobs[correlation][key] for key in ('task_id', 'agent_id', 'correlation_id')},
                'outcome': 'FOUND', 'remote_ref': self.jobs[correlation]} if correlation in self.jobs else {
            'outcome': 'UNKNOWN'}

    def result(self, correlation):
        return {'schema_version': '1', 'remote_ref': self.jobs[correlation], 'finished_at': utcnow(),
                'status': 'SUCCEEDED', 'result_refs': ['artifact:test']}


def only_job(ledger, task_id):
    # Claim the intended test job without touching other tests' pending work.
    with ledger.connect() as conn:
        return conn.execute("UPDATE outbox SET state='CLAIMED',lease_token=%s,lease_until=now()+interval '1 minute',"
                            'attempts=attempts+1 WHERE task_id=%s AND kind=%s RETURNING *',
                            (uuid4(), task_id, 'DISPATCH')).fetchone()


def test_acceptance_timeout_reconciles_without_second_submit(owner, task_request):
    ledger, tenant, principal = owner
    task, _ = ledger.create_task(tenant, principal, {**task_request, 'requested_agent': 'mock-research'})
    remote = MockAgent(lose_response=True)
    worker = Dispatcher(ledger, remote)
    job = only_job(ledger, task['id'])
    with pytest.raises(RemoteUnavailable):
        worker.process(job)
    worker.uncertain(job)
    assert ledger.get_task(tenant, principal, task['id'])['state'] == 'UNKNOWN'
    restarted = Dispatcher(Ledger(ledger.database_url), remote)
    restarted.process(only_job(ledger, task['id']))
    restarted.process(only_job(ledger, task['id']))
    assert ledger.get_task(tenant, principal, task['id'])['state'] == 'SUCCEEDED'
    assert remote.calls == 1
    with ledger.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM outbox WHERE task_id=%s AND kind='DELIVERY'",
                            (task['id'],)).fetchone()['n'] == 1


def test_crash_after_prepare_never_blindly_submits(owner, task_request):
    ledger, tenant, principal = owner
    task, _ = ledger.create_task(tenant, principal, {**task_request, 'requested_agent': 'mock-research'})
    remote = MockAgent()
    worker = Dispatcher(ledger, remote)
    worker.prepare(only_job(ledger, task['id']))  # Crash before network call: cannot prove it later.
    job = only_job(ledger, task['id'])
    with pytest.raises(RemoteUnavailable):
        worker.process(job)
    worker.uncertain(job)
    assert remote.calls == 0
    assert ledger.get_task(tenant, principal, task['id'])['state'] == 'UNKNOWN'


def test_sensitive_task_is_held_and_cancelled_task_never_dispatches(owner, task_request):
    ledger, tenant, principal = owner
    task, _ = ledger.create_task(tenant, principal, {**task_request, 'intent': 'delegate'})
    remote = MockAgent()
    Dispatcher(ledger, remote).process(only_job(ledger, task['id']))
    assert ledger.get_task(tenant, principal, task['id'])['state'] == 'AWAITING_APPROVAL'
    assert remote.calls == 0


def test_wrong_remote_binding_is_rejected(owner, task_request):
    ledger, tenant, principal = owner
    task, _ = ledger.create_task(tenant, principal, {**task_request, 'requested_agent': 'mock-research'})
    worker = Dispatcher(ledger, MockAgent())
    job = only_job(ledger, task['id'])
    _, payload = worker.prepare(job)
    ref = worker.remote.submit(payload)
    with pytest.raises(ValueError, match='binding'):
        worker.accept(job, payload['attempt'], {**ref, 'task_id': str(uuid4())})
    assert ledger.get_task(tenant, principal, task['id'])['state'] == 'DISPATCHING'


def test_callback_duplicate_and_terminal_conflict(owner, task_request):
    from xmux_control.auth import Actor
    from xmux_control.db.ledger import Conflict
    from xmux_control.observations import record_callback
    ledger, tenant, principal = owner
    task, _ = ledger.create_task(tenant, principal, {**task_request, 'requested_agent': 'mock-research'})
    remote = MockAgent()
    worker = Dispatcher(ledger, remote)
    worker.process(only_job(ledger, task['id']))
    correlation = next(iter(remote.jobs))
    callback = {'schema_version': '1', 'callback_id': str(uuid4()), 'sequence': 1, 'emitted_at': utcnow(),
                'kind': 'result', 'result': remote.result(correlation)}
    actor = Actor(tenant, principal, frozenset({'tasks:observe'}), 'multiplexer')
    assert record_callback(ledger, actor, callback)
    assert not record_callback(ledger, actor, callback)
    with pytest.raises(Conflict):
        record_callback(ledger, actor, {**callback, 'result': {**callback['result'], 'result_refs': ['changed']}})
    assert ledger.get_task(tenant, principal, task['id'])['state'] == 'SUCCEEDED'


def test_stale_absence_evidence_cannot_enable_retry(owner, task_request):
    ledger, tenant, principal = owner
    task, _ = ledger.create_task(tenant, principal, {**task_request, 'requested_agent': 'mock-research'})
    remote = MockAgent()
    worker = Dispatcher(ledger, remote)
    _, payload = worker.prepare(only_job(ledger, task['id']))
    attempt = payload['attempt']
    remote.reconcile = lambda _: {
        'schema_version': '1', 'outcome': 'ABSENT', 'checked_at': '2020-01-01T00:00:00Z',
        **{key: attempt[key] for key in ('task_id', 'agent_id', 'correlation_id')},
        'evidence': {'authoritative': True, 'evidence_ref': 'proof:test', 'never_accepted': True,
                     'acceptance_closed': True, 'covers_dispatch_started_at': attempt['started_at'],
                     'valid_until': '2020-01-01T00:01:00Z'}}
    with pytest.raises(RemoteUnavailable, match='stale_absence'):
        worker.process(only_job(ledger, task['id']))
    assert remote.calls == 0
    assert ledger.get_task(tenant, principal, task['id'])['state'] == 'DISPATCHING'


def test_fresh_absence_retries_with_new_correlation(owner, task_request):
    from datetime import UTC, datetime, timedelta
    ledger, tenant, principal = owner
    task, _ = ledger.create_task(tenant, principal, {**task_request, 'requested_agent': 'mock-research'})
    remote = MockAgent()
    worker = Dispatcher(ledger, remote)
    _, payload = worker.prepare(only_job(ledger, task['id']))
    attempt = payload['attempt']
    remote.reconcile = lambda _: {
        'schema_version': '1', 'outcome': 'ABSENT', 'checked_at': utcnow(),
        **{key: attempt[key] for key in ('task_id', 'agent_id', 'correlation_id')},
        'evidence': {'authoritative': True, 'evidence_ref': 'proof:test', 'never_accepted': True,
                     'acceptance_closed': True, 'covers_dispatch_started_at': attempt['started_at'],
                     'valid_until': (datetime.now(UTC) + timedelta(seconds=30)).isoformat().replace('+00:00', 'Z')}}
    worker.process(only_job(ledger, task['id']))
    assert remote.calls == 1
    assert attempt['correlation_id'] not in remote.jobs
    assert ledger.get_task(tenant, principal, task['id'])['state'] == 'RUNNING'
    with ledger.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM dispatch_attempts WHERE task_id=%s',
                            (task['id'],)).fetchone()['n'] == 2


def test_operator_replay_preserves_uncertain_dispatch(owner, task_request):
    from xmux_control.operator import replay
    ledger, tenant, principal = owner
    task, _ = ledger.create_task(tenant, principal, {**task_request, 'requested_agent': 'mock-research'})
    remote = MockAgent()
    worker = Dispatcher(ledger, remote)
    job = only_job(ledger, task['id'])
    worker.prepare(job)
    with ledger.connect() as conn:
        conn.execute("UPDATE outbox SET state='DEAD' WHERE id=%s", (job['id'],))
    replay(ledger, job['id'], tenant=tenant, principal=principal)
    with pytest.raises(RemoteUnavailable):
        worker.process(only_job(ledger, task['id']))
    assert remote.calls == 0


def test_restart_after_absence_rechecks_and_can_retry(owner, task_request):
    from datetime import UTC, datetime, timedelta

    from psycopg.types.json import Jsonb
    ledger, tenant, principal = owner
    task, _ = ledger.create_task(tenant, principal, {**task_request, 'requested_agent': 'mock-research'})
    remote = MockAgent()
    worker = Dispatcher(ledger, remote)
    _, payload = worker.prepare(only_job(ledger, task['id']))
    attempt = payload['attempt']
    observation = {
        'schema_version': '1', 'outcome': 'ABSENT', 'checked_at': utcnow(),
        **{key: attempt[key] for key in ('task_id', 'agent_id', 'correlation_id')},
        'evidence': {'authoritative': True, 'evidence_ref': 'proof:test', 'never_accepted': True,
                     'acceptance_closed': True, 'covers_dispatch_started_at': attempt['started_at'],
                     'valid_until': (datetime.now(UTC)+timedelta(seconds=30)).isoformat().replace('+00:00', 'Z')}}
    with ledger.connect() as conn:
        ledger.transition(conn, task['id'], 'DISPATCHING', 'RETRY_PENDING', 'test')
        conn.execute('UPDATE dispatch_attempts SET absence_evidence=%s WHERE task_id=%s',
                     (Jsonb(observation), task['id']))
    calls = []
    def reconcile(correlation):
        calls.append(correlation)
        return observation
    remote.reconcile = reconcile
    worker.process(only_job(ledger, task['id']))
    assert calls == [attempt['correlation_id']]
    assert remote.calls == 1
    assert ledger.get_task(tenant, principal, task['id'])['state'] == 'RUNNING'
