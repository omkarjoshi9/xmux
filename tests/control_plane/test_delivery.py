from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb
from xmux_control.db.ledger import NotFound
from xmux_control.delivery import Delivery
from xmux_control.dispatch import RemoteUnavailable


def claim(ledger, task_id, kind='DELIVERY'):
    with ledger.connect() as conn:
        return conn.execute("UPDATE outbox SET state='CLAIMED',lease_token=%s,lease_until=now()+interval '1 minute',"
                            'attempts=attempts+1 WHERE task_id=%s AND kind=%s RETURNING *',
                            (uuid4(), task_id, kind)).fetchone()


def completed(owner, request):
    ledger, tenant, principal = owner
    event = {'schema_version': '1', 'tenant_id': str(tenant), 'platform': 'telegram',
             'platform_event_id': str(uuid4()), 'event_id': str(uuid4()), 'event_type': 'direct_message',
             'occurred_at': '2026-11-21T12:00:00Z', 'actor_external_id': 'user', 'text': 'hello',
             'urls': [], 'attachments': [], 'reply_target': {'channel_id': 'original', 'message_id': '1'},
             'auth_context': {'identity_mapping_ref': 'mapping'}}
    task, _ = ledger.ingest_event(tenant, principal, event, request)
    with ledger.connect() as conn:
        conn.execute("UPDATE tasks SET state='SUCCEEDED',result=%s WHERE id=%s",
                     (Jsonb({'summary': 'Complete', 'result_refs': ['artifact:1']}), task['id']))
        ledger.enqueue(conn, task['id'], 'DELIVERY', f"delivery:{task['id']}")
    return task


class Gateway:
    calls = 0
    status = 'CONFIRMED'

    def submit(self, payload):
        from xmux_control.contracts import validate
        validate('DeliveryRequest', payload)
        assert payload['reply_target']['channel_id'] == 'original'
        self.calls += 1
        self.payload = payload
        raise RemoteUnavailable('accepted_then_timeout')

    def reconcile(self, key):
        return {'task_id': self.payload['task_id'], 'idempotency_key': key,
                'status': self.status, 'error_code': 'permission_denied' if self.status == 'FAILED' else None}


@pytest.mark.parametrize('status', ['CONFIRMED', 'FAILED'])
def test_timeout_restart_and_permanent_failure(owner, task_request, status):
    ledger, tenant, principal = owner
    task = completed(owner, task_request)
    remote = Gateway()
    remote.status = status
    worker = Delivery(ledger, remote)
    job = claim(ledger, task['id'])
    with pytest.raises(RemoteUnavailable):
        worker.process(job)
    worker.defer(job)
    Delivery(ledger, remote).process(claim(ledger, task['id']))
    assert remote.calls == 1
    assert worker.status(tenant, principal, task['id'])['state'] == status
    assert ledger.get_task(tenant, principal, task['id'])['state'] == 'SUCCEEDED'
    with pytest.raises(NotFound):
        worker.status(tenant, uuid4(), task['id'])


def test_accepted_is_not_confirmed_and_retry_requires_fence(owner, task_request):
    ledger, tenant, principal = owner
    task = completed(owner, task_request)

    class Accepted(Gateway):
        def submit(self, payload):
            self.payload = payload
            self.calls += 1
            return {'schema_version': '1', 'delivery_id': 'gateway:1', 'status': 'ACCEPTED'}

    remote = Accepted()
    worker = Delivery(ledger, remote)
    worker.process(claim(ledger, task['id']))
    assert worker.status(tenant, principal, task['id'])['state'] == 'ACCEPTED'
    remote.status = 'RETRY_READY'
    with pytest.raises(ValueError, match='unsafe_retry'):
        worker.process(claim(ledger, task['id']))
    assert remote.calls == 1
    remote.status = 'CONFIRMED'
    worker.process(claim(ledger, task['id']))
    assert worker.status(tenant, principal, task['id'])['state'] == 'CONFIRMED'


def test_http_adapter_posts_exact_contract():
    import json

    import httpx
    from xmux_control.contracts import validate
    from xmux_control.delivery import HttpGateway
    payload = {'schema_version': '1', 'task_id': str(uuid4()), 'idempotency_key': 'stable',
               'channel': 'telegram', 'reply_target': {'channel_id': 'original'}, 'text': 'done', 'artifacts': []}

    def handle(request):
        assert request.method == 'POST' and request.url.path == '/configured/delivery'
        assert json.loads(request.content) == payload
        validate('DeliveryRequest', json.loads(request.content))
        return httpx.Response(200, json={'schema_version': '1', 'delivery_id': 'receipt:1', 'status': 'ACCEPTED'})

    client = httpx.Client(base_url='https://gateway.test', transport=httpx.MockTransport(handle))
    receipt = HttpGateway(client, submit_path='/configured/delivery', status_path=None).submit(payload)
    validate('DeliveryReceipt', receipt)


def test_fenced_retry_and_bounded_unknown_recovery(owner, task_request):
    ledger, tenant, principal = owner
    task = completed(owner, task_request)
    remote = Gateway()
    worker = Delivery(ledger, remote)
    with pytest.raises(RemoteUnavailable):
        worker.process(claim(ledger, task['id']))
    remote.reconcile = lambda key: {'task_id': str(task['id']), 'idempotency_key': key,
                                    'status': 'RETRY_READY', 'prior_attempt_fenced': True}
    worker.process(claim(ledger, task['id']))
    with pytest.raises(RemoteUnavailable):
        Delivery(ledger, remote).process(claim(ledger, task['id']))
    assert remote.calls == 2
    with ledger.connect() as conn:
        conn.execute("UPDATE outbox SET attempts=8 WHERE task_id=%s AND kind='DELIVERY'", (task['id'],))
    job = claim(ledger, task['id'])
    worker.defer(job)
    with ledger.connect() as conn:
        assert conn.execute('SELECT state FROM outbox WHERE id=%s', (job['id'],)).fetchone()['state'] == 'DEAD'
    assert worker.status(tenant, principal, task['id'])['state'] == 'UNKNOWN'


def test_missing_target_is_explicit_failure(owner, task_request):
    ledger, tenant, principal = owner
    task, _ = ledger.create_task(tenant, principal, task_request)
    with ledger.connect() as conn:
        conn.execute("UPDATE tasks SET state='SUCCEEDED' WHERE id=%s", (task['id'],))
        ledger.enqueue(conn, task['id'], 'DELIVERY', f"delivery:{task['id']}")
    worker = Delivery(ledger, None)
    worker.process(claim(ledger, task['id']))
    assert worker.status(tenant, principal, task['id'])['error_code'] == 'reply_target_unavailable'
