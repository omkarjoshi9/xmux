"""Durable delivery. Adapter observations are authoritative, key-bound gateway evidence."""
from uuid import uuid4

import httpx
from jsonschema import ValidationError
from psycopg.types.json import Jsonb

from .contracts import validate
from .db.ledger import Conflict, NotFound
from .dispatch import Dispatcher, RemoteUnavailable


class HttpGateway:
    def __init__(self, client, *, submit_path, status_path):
        self.client, self.submit_path, self.status_path = client, submit_path, status_path

    def submit(self, payload):
        if not self.submit_path:
            raise RemoteUnavailable('delivery_endpoint_unconfigured')
        response = self.client.post(self.submit_path, json=payload)
        response.raise_for_status()
        return response.json()

    def reconcile(self, key):
        if not self.status_path:
            raise RemoteUnavailable('delivery_lookup_unconfigured')
        response = self.client.get(self.status_path.format(key=key))
        response.raise_for_status()
        return response.json()


class Delivery(Dispatcher):
    def status(self, tenant, principal, task_id):
        with self.ledger.connect() as conn:
            self.ledger.get_task(tenant, principal, task_id, connection=conn)
            row = conn.execute('SELECT * FROM deliveries WHERE task_id=%s', (task_id,)).fetchone()
            if not row:
                raise NotFound('delivery_not_found')
            return row

    def tick(self):
        job = self.ledger.claim(['DELIVERY'])
        if not job:
            return False
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

    def defer(self, job):
        with self.ledger.connect() as conn:
            self.load_locked(conn, job)
            conn.execute("UPDATE outbox SET state=%s,lease_until=NULL,available_at=now()+%s*interval '1 second',"
                         "last_error='delivery_reconciliation_required' WHERE id=%s",
                         ('DEAD' if job['attempts'] >= 8 else 'PENDING', min(300, 2**min(job['attempts'],8)), job['id']))

    def process(self, job):
        with self.ledger.connect() as conn:
            task = self.load_locked(conn, job)
            if task['state'] not in {'SUCCEEDED', 'FAILED', 'CANCELLED', 'REJECTED'}:
                raise Conflict('task_not_terminal')
            row = conn.execute('SELECT * FROM deliveries WHERE task_id=%s FOR UPDATE', (task['id'],)).fetchone()
            fresh = row is None
            if fresh:
                event = conn.execute('SELECT payload FROM channel_events WHERE id=%s AND tenant_id=%s AND principal_id=%s',
                                     (task['source_event_id'], task['tenant_id'], task['principal_id'])).fetchone()
                result = task['result'] or {}
                payload = {'schema_version': '1', 'task_id': str(task['id']),
                           'idempotency_key': 'delivery:' + str(task['id']),
                           'channel': event['payload']['platform'] if event else '',
                           'reply_target': event['payload']['reply_target'] if event else {},
                           'text': result.get('summary', 'Task ' + task['state'].lower()),
                           'artifacts': result.get('result_refs', [])}
                if event:
                    validate('DeliveryRequest', payload)
                row = conn.execute('INSERT INTO deliveries(id,task_id,state,request,error_code) VALUES(%s,%s,%s,%s,%s) RETURNING *',
                                   (uuid4(), task['id'], 'UNKNOWN' if event else 'FAILED', Jsonb(payload),
                                    None if event else 'reply_target_unavailable')).fetchone()
            if row['state'] in {'CONFIRMED', 'FAILED'}:
                self.ledger.finish(conn, job)
                return
        payload = row['request']
        if fresh or row['state'] == 'RETRY_READY':
            # Commit the uncertain intent before every potentially visible post.
            with self.ledger.connect() as conn:
                self.load_locked(conn, job)
                conn.execute("UPDATE deliveries SET state='UNKNOWN' WHERE task_id=%s", (task['id'],))
            receipt = self.remote.submit(payload)
            validate('DeliveryReceipt', receipt)
            state, error = 'ACCEPTED', None
        else:
            receipt = self.remote.reconcile(payload['idempotency_key'])
            if receipt['idempotency_key'] != payload['idempotency_key'] or receipt['task_id'] != str(task['id']):
                raise ValueError('delivery_binding_mismatch')
            state = receipt['status']
            if state not in {'ACCEPTED', 'CONFIRMED', 'FAILED', 'RETRY_READY'}:
                raise RemoteUnavailable('delivery_unknown')
            # RETRY_READY promises prior attempt cannot deliver, including in-flight requests.
            if state == 'RETRY_READY' and receipt.get('prior_attempt_fenced') is not True:
                raise ValueError('unsafe_retry')
            error = receipt.get('error_code')
            if state == 'FAILED' and not error:
                raise ValueError('missing_failure_reason')
        with self.ledger.connect() as conn:
            self.load_locked(conn, job)
            conn.execute('UPDATE deliveries SET state=%s,receipt=%s,error_code=%s,updated_at=now() WHERE task_id=%s',
                         (state, Jsonb(receipt), error, task['id']))
            if state in {'CONFIRMED', 'FAILED'}:
                self.ledger.finish(conn, job)
                return
        self.defer(job)


def router(ledger, authenticate):
    from uuid import UUID

    from fastapi import APIRouter, Depends
    routes = APIRouter()
    authenticated = Depends(authenticate)

    @routes.get('/v1/tasks/{task_id}/delivery')
    def status(task_id: UUID, actor=authenticated):
        actor.require('tasks:read')
        return Delivery(ledger, None).status(actor.tenant, actor.principal, task_id)

    return routes
