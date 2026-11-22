"""Remote cancellation intent is durable; acknowledgements never imply completion."""
from uuid import uuid4

from psycopg.types.json import Jsonb

from .contracts import validate
from .db.ledger import Conflict
from .delivery import Delivery
from .dispatch import RemoteUnavailable


class HttpCancellation:
    def __init__(self, client, *, cancel_path, status_path):
        self.client, self.cancel_path, self.status_path = client, cancel_path, status_path

    def submit(self, payload):
        if not self.cancel_path:
            raise RemoteUnavailable('cancellation_unconfigured')
        response = self.client.post(self.cancel_path, json=payload)
        response.raise_for_status()
        return response.json()

    def reconcile(self, key):
        if not self.status_path:
            raise RemoteUnavailable('cancellation_lookup_unconfigured')
        response = self.client.get(self.status_path.format(key=key))
        response.raise_for_status()
        return response.json()


def persist_capabilities(conn, task_id, capabilities):
    """Call with trusted registry evidence in the remote-acceptance transaction."""
    validate('AgentCapabilities', capabilities)
    row = conn.execute('SELECT agent_id FROM remote_jobs WHERE task_id=%s FOR UPDATE', (task_id,)).fetchone()
    if not row or row['agent_id'] != capabilities['agent_id']:
        raise Conflict('capability_binding_mismatch')
    conn.execute('UPDATE remote_jobs SET capabilities=%s WHERE task_id=%s', (Jsonb(capabilities), task_id))


class Cancellation(Delivery):
    def request(self, tenant, principal, task_id):
        with self.ledger.connect() as conn:
            self.ledger.get_task(tenant, principal, task_id, connection=conn)
            task = conn.execute('SELECT * FROM tasks WHERE id=%s FOR UPDATE', (task_id,)).fetchone()
            if task['state'] in {'QUEUED', 'AWAITING_APPROVAL'}:
                self.ledger.transition(conn, task_id, task['state'], 'CANCELLED', str(principal))
                return {'state': 'CONFIRMED'}
            if task['state'] in {'SUCCEEDED', 'FAILED', 'CANCELLED', 'REJECTED'}:
                return {'state': 'ALREADY_TERMINAL'}
            remote = conn.execute('SELECT * FROM remote_jobs WHERE task_id=%s', (task_id,)).fetchone()
            if not remote:
                raise Conflict('remote_reference_unavailable')
            validate('RemoteTaskRef', remote['receipt'])
            if remote['receipt']['task_id'] != str(task_id) or remote['receipt']['agent_id'] != remote['agent_id'] or remote['receipt']['correlation_id'] != str(remote['correlation_id']) or remote['receipt']['remote_task_id'] != remote['remote_task_id']:
                raise Conflict('remote_reference_mismatch')
            capabilities = remote['capabilities']
            if capabilities:
                validate('AgentCapabilities', capabilities)
            supported = capabilities and capabilities['agent_id'] == remote['agent_id'] and capabilities['supports_cancel']
            row = conn.execute('INSERT INTO cancellations(task_id,id,state,remote_ref) VALUES(%s,%s,%s,%s) '
                               'ON CONFLICT(task_id) DO UPDATE SET task_id=excluded.task_id RETURNING *',
                               (task_id, uuid4(), 'PENDING' if supported else 'UNSUPPORTED', Jsonb(remote['receipt']))).fetchone()
            if row['state'] != 'UNSUPPORTED':
                self.ledger.enqueue(conn, task_id, 'CANCEL', f'cancel:{task_id}')
            return row

    def tick(self):
        # Reuse delivery's fenced error handling with this worker's queue.
        job = self.ledger.claim(['CANCEL'])
        if not job:
            return False
        import httpx
        from jsonschema import ValidationError
        try:
            self.process(job)
        except (httpx.HTTPError, RemoteUnavailable, ValidationError, ValueError, KeyError):
            try:
                self.defer(job)
            except Conflict:
                pass
        except Conflict:
            pass
        return True

    def process(self, job):
        with self.ledger.connect() as conn:
            task = self.load_locked(conn, job)
            row = conn.execute('SELECT * FROM cancellations WHERE task_id=%s FOR UPDATE', (task['id'],)).fetchone()
            if row['state'] in {'CONFIRMED', 'UNSUPPORTED', 'ALREADY_TERMINAL'}:
                self.ledger.finish(conn, job)
                return
            fresh = row['state'] == 'PENDING'
            conn.execute("UPDATE cancellations SET state='UNKNOWN' WHERE task_id=%s", (task['id'],))
        payload = {'schema_version': '1', 'cancellation_id': str(row['id']), 'remote_ref': row['remote_ref']}
        receipt = self.remote.submit(payload) if fresh else self.remote.reconcile(str(row['id']))
        validate('CancellationReceipt', receipt)
        if receipt['cancellation_id'] != str(row['id']) or receipt['remote_ref'] != row['remote_ref']:
            raise ValueError('cancellation_binding_mismatch')
        state = receipt['outcome']
        with self.ledger.connect() as conn:
            task = self.load_locked(conn, job)
            conn.execute('UPDATE cancellations SET state=%s,receipt=%s WHERE task_id=%s',
                         (state, Jsonb(receipt), task['id']))
            if state == 'CONFIRMED' and task['state'] in {'RUNNING', 'UNKNOWN'}:
                self.ledger.transition(conn, task['id'], task['state'], 'CANCELLED', 'confirmed-remote-cancellation')
                self.ledger.enqueue(conn, task['id'], 'DELIVERY', f"delivery:{task['id']}")
            if state in {'CONFIRMED', 'UNSUPPORTED', 'ALREADY_TERMINAL'}:
                self.ledger.finish(conn, job)
                return
        self.defer(job)


def router(ledger, authenticate):
    from uuid import UUID

    from fastapi import APIRouter, Depends
    routes = APIRouter()
    authenticated = Depends(authenticate)

    @routes.post('/v1/tasks/{task_id}/cancel')
    def cancel(task_id: UUID, actor=authenticated):
        actor.require('tasks:cancel')
        Cancellation(ledger, None).request(actor.tenant, actor.principal, task_id)
        from .api import task_status
        result = task_status(ledger.get_task(actor.tenant, actor.principal, task_id))
        return result

    return routes
