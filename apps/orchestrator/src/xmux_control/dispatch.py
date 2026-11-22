"""Durable dispatch: uncertain acceptance is reconciliation, never a blind resend."""
import random
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
from jsonschema import ValidationError
from psycopg.types.json import Jsonb

from xmux_control.contracts import validate
from xmux_control.db.ledger import Conflict, fingerprint


def utcnow():
    return datetime.now(UTC).isoformat().replace('+00:00', 'Z')


class RemoteUnavailable(Exception):
    pass


class HttpMultiplexer:
    """Configured boundary only; selection and execution belong to the multiplexer."""
    def __init__(self, settings, *, client=None, reconcile_path=None, result_path=None, selection_path=None):
        self.client = client or httpx.Client(base_url=settings.multiplexer_url,
            headers={'Authorization': 'Bearer ' + settings.multiplexer_token}, timeout=10)
        self.reconcile_path = reconcile_path
        self.result_path = result_path
        self.selection_path = selection_path

    def select(self, task):
        if not self.selection_path:
            raise RemoteUnavailable('selection_unsupported')
        response = self.client.post(self.selection_path, json={
            'tenant_id': str(task['tenant_id']), 'principal_id': str(task['principal_id']),
            'task': task['request']})
        response.raise_for_status()
        capabilities = response.json()
        validate('AgentCapabilities', capabilities)
        return capabilities

    def submit(self, payload):
        response = self.client.post('/v1/dispatch', json=payload)
        response.raise_for_status()
        return response.json()

    def reconcile(self, correlation):
        if not self.reconcile_path:
            raise RemoteUnavailable('reconciliation_unsupported')
        response = self.client.get(self.reconcile_path.format(correlation=correlation))
        response.raise_for_status()
        return response.json()

    def result(self, correlation):
        if not self.result_path:
            raise RemoteUnavailable('polling_unsupported')
        response = self.client.get(self.result_path.format(correlation=correlation))
        response.raise_for_status()
        return response.json()


class Dispatcher:
    def __init__(self, ledger, remote):
        self.ledger, self.remote = ledger, remote

    def tick(self):
        job = self.ledger.claim(['DISPATCH'], lease_seconds=60)
        if not job:
            return False
        try:
            self.process(job)
        except (httpx.HTTPError, RemoteUnavailable, ValidationError, ValueError, KeyError):
            try:
                self.uncertain(job)
            except Conflict:
                pass  # The new lease owner handles recovery.
        except Conflict:
            # Another worker or cancellation won the fence. Never resend here.
            pass
        return True

    def load_locked(self, conn, job):
        lease = conn.execute("SELECT id FROM outbox WHERE id=%s AND lease_token=%s "
                             "AND state='CLAIMED' AND lease_until>now() FOR UPDATE",
                             (job['id'], job['lease_token'])).fetchone()
        if not lease:
            raise Conflict('stale_lease')
        return conn.execute('SELECT * FROM tasks WHERE id=%s FOR UPDATE', (job['task_id'],)).fetchone()

    def prepare(self, job, *, retry=False, capabilities=None):
        with self.ledger.connect() as conn:
            task = self.load_locked(conn, job)
            state = task['state']
            if state in {'SUCCEEDED', 'FAILED', 'CANCELLED', 'REJECTED', 'AWAITING_APPROVAL'}:
                self.ledger.finish(conn, job)
                return None
            if retry:
                proof = conn.execute('SELECT absence_evidence,evidence FROM dispatch_attempts WHERE task_id=%s '
                                     'ORDER BY created_at DESC LIMIT 1', (task['id'],)).fetchone()
                evidence = proof['absence_evidence'] if proof else None
                now = datetime.now(UTC)
                if (not evidence or now >= datetime.fromisoformat(evidence['evidence']['valid_until'])
                        or (now - datetime.fromisoformat(evidence['checked_at'])).total_seconds() > 60):
                    raise RemoteUnavailable('stale_absence_evidence')
            request = proof['evidence']['task'] if retry else task['request']
            if state == 'QUEUED' or (state == 'RETRY_PENDING' and retry):
                if task['intent'] not in {'research', 'summarize'}:
                    self.ledger.transition(conn, task['id'], state, 'AWAITING_APPROVAL', 'policy')
                    self.ledger.finish(conn, job)
                    return None
                if capabilities is not None:
                    validate('AgentCapabilities', capabilities)
                    if (task['intent'] not in capabilities['operations']
                            or (request.get('requested_agent')
                                and request['requested_agent'] != capabilities['agent_id'])):
                        raise RemoteUnavailable('capability_mismatch')
                    request = {**request, 'requested_agent': capabilities['agent_id']}
                if not request.get('requested_agent'):
                    # Selection requires the actual registry/routing service, never guess an agent.
                    raise RemoteUnavailable('selection_unavailable')
                task = self.ledger.transition(conn, task['id'], state, 'DISPATCHING', 'worker')
                correlation = uuid4()
                digest = fingerprint(task['tenant_id'], task['principal_id'], 'task.create', request)
                attempt = {'schema_version': '1', 'task_id': str(task['id']), 'task_version': task['version'],
                    'tenant_id': str(task['tenant_id']), 'principal_id': str(task['principal_id']),
                    'agent_id': request['requested_agent'], 'correlation_id': str(correlation),
                    'remote_idempotency_key': str(task['id']), 'request_fingerprint': {
                        'profile': 'xmux-jcs-sha256-v1', 'digest': digest}, 'started_at': utcnow()}
                delegation = {'schema_version': '1', 'grant_id': str(uuid4()), 'actor_service_id': 'orchestrator',
                    'audience': 'multiplexer', 'tenant_id': str(task['tenant_id']),
                    'principal_id': str(task['principal_id']), 'task_id': str(task['id']),
                    'task_version': task['version'], 'agent_id': request['requested_agent'],
                    'scope': 'tasks:dispatch', 'action_fingerprint': digest, 'approval_refs': [],
                    'issued_at': utcnow(), 'expires_at': (datetime.now(UTC)+timedelta(minutes=5))
                    .isoformat().replace('+00:00', 'Z')}
                payload = {'schema_version': '1', 'attempt': attempt, 'task': request, 'delegation': delegation}
                validate('DispatchRequest', payload)
                conn.execute('INSERT INTO dispatch_attempts(id,task_id,correlation_id,state,evidence) '
                             'VALUES(%s,%s,%s,%s,%s)',
                             (uuid4(), task['id'], correlation, 'PREPARED', Jsonb(payload)))
                return 'submit', payload
            attempt = conn.execute('SELECT * FROM dispatch_attempts WHERE task_id=%s '
                                   'ORDER BY created_at DESC LIMIT 1', (task['id'],)).fetchone()
            if not attempt:
                raise RemoteUnavailable('missing_dispatch_evidence')
            return ('poll' if state == 'RUNNING' else 'reconcile'), attempt['evidence']

    def process(self, job):
        capabilities = None
        # Registry selection is remote I/O and cannot run under a database lock.
        if isinstance(self.remote, HttpMultiplexer):
            with self.ledger.connect() as conn:
                task = self.load_locked(conn, job)
            if task['state'] == 'QUEUED' and task['intent'] in {'research', 'summarize'}:
                capabilities = self.remote.select(task)
        prepared = self.prepare(job, capabilities=capabilities)
        if not prepared:
            return
        mode, payload = prepared
        attempt = payload['attempt']
        correlation = attempt['correlation_id']
        if mode == 'submit':
            ref = self.remote.submit(payload)
            self.accept(job, attempt, ref)
        elif mode == 'reconcile':
            observation = self.remote.reconcile(correlation)
            if observation.get('outcome') == 'UNKNOWN':
                raise RemoteUnavailable('remote_outcome_unknown')
            validate('ReconciliationOutcome', observation)
            for field in ('task_id', 'agent_id', 'correlation_id'):
                if observation[field] != attempt[field]:
                    raise ValueError('reconciliation_binding_mismatch')
            if observation.get('outcome') == 'ABSENT':
                evidence = observation['evidence']
                now = datetime.now(UTC)
                checked = datetime.fromisoformat(observation['checked_at'])
                expires = datetime.fromisoformat(evidence['valid_until'])
                if (evidence['covers_dispatch_started_at'] != attempt['started_at']
                        or not checked <= now < expires or (now - checked).total_seconds() > 60):
                    raise RemoteUnavailable('stale_absence_evidence')
                with self.ledger.connect() as conn:
                    task = self.load_locked(conn, job)
                    if task['state'] == 'DISPATCHING':
                        task = self.ledger.transition(conn, task['id'], 'DISPATCHING', 'UNKNOWN', 'reconcile')
                    if task['state'] != 'RETRY_PENDING':
                        self.ledger.transition(conn, task['id'], task['state'], 'RETRY_PENDING', 'confirmed-absence')
                    conn.execute('UPDATE dispatch_attempts SET absence_evidence=%s WHERE correlation_id=%s',
                                 (Jsonb(observation), correlation))
                    count = conn.execute('SELECT count(*) AS n FROM dispatch_attempts WHERE task_id=%s',
                                         (task['id'],)).fetchone()['n']
                    if count >= 3:
                        conn.execute("UPDATE outbox SET state='DEAD',lease_until=NULL,last_error=%s WHERE id=%s",
                                     ('dispatch_retry_exhausted', job['id']))
                        return
                # Fresh proof is consumed in this invocation. Restart must obtain new proof.
                prepared = self.prepare(job, retry=True)
                if prepared:
                    _, retry_payload = prepared
                    self.accept(job, retry_payload['attempt'], self.remote.submit(retry_payload))
                return
            if observation.get('outcome') != 'FOUND':
                raise RemoteUnavailable('remote_outcome_unknown')
            self.accept(job, attempt, observation['remote_ref'])
        else:
            result = self.remote.result(correlation)
            validate('RemoteResult', result)
            self.check_binding(attempt, result['remote_ref'])
            with self.ledger.connect() as conn:
                task = self.load_locked(conn, job)
                stored = conn.execute('SELECT receipt FROM remote_jobs WHERE task_id=%s',
                                      (task['id'],)).fetchone()
                if not stored or stored['receipt'] != result['remote_ref']:
                    raise ValueError('remote_binding_mismatch')
                self.ledger.transition(conn, task['id'], task['state'], result['status'], 'remote-result')
                conn.execute('UPDATE tasks SET result=%s,error_code=%s WHERE id=%s',
                             (Jsonb(result), result.get('error', {}).get('code'), task['id']))
                self.ledger.enqueue(conn, task['id'], 'DELIVERY', f"delivery:{task['id']}")
                self.ledger.finish(conn, job)

    @staticmethod
    def check_binding(attempt, ref):
        validate('RemoteTaskRef', ref)
        for key in ('task_id', 'agent_id', 'correlation_id'):
            if ref[key] != attempt[key]:
                raise ValueError('remote_binding_mismatch')

    def accept(self, job, attempt, ref):
        self.check_binding(attempt, ref)
        with self.ledger.connect() as conn:
            task = self.load_locked(conn, job)
            self.ledger.transition(conn, task['id'], task['state'], 'RUNNING', 'remote-acceptance')
            conn.execute('INSERT INTO remote_jobs(id,task_id,agent_id,remote_task_id,correlation_id,receipt) '
                         'VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(task_id) DO NOTHING',
                         (uuid4(), task['id'], ref['agent_id'], ref['remote_task_id'], ref['correlation_id'],
                          Jsonb(ref)))
            conn.execute('UPDATE tasks SET assigned_agent=%s WHERE id=%s', (ref['agent_id'], task['id']))
            conn.execute("UPDATE outbox SET state='PENDING',lease_until=NULL,available_at=now() "
                         'WHERE id=%s', (job['id'],))

    def uncertain(self, job):
        with self.ledger.connect() as conn:
            task = self.load_locked(conn, job)
            if task['state'] in {'DISPATCHING', 'RUNNING'}:
                self.ledger.transition(conn, task['id'], task['state'], 'UNKNOWN', 'reconciliation-required')
            delay = min(300, 2 ** min(job['attempts'], 8)) + random.uniform(0, 1)
            conn.execute("UPDATE outbox SET state=%s,lease_until=NULL,available_at=now()+%s*interval '1 second', "
                         'last_error=%s WHERE id=%s',
                         ('DEAD' if job['attempts'] >= 8 else 'PENDING', delay,
                          'remote_outcome_unknown', job['id']))
