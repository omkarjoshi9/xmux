"""Short PostgreSQL transactions; remote calls must happen outside this module."""
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import psycopg
import rfc8785
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


class Conflict(Exception):
    pass


class NotFound(Exception):
    pass


TRANSITIONS = {
    'RECEIVED': {'REJECTED', 'QUEUED'},
    'QUEUED': {'AWAITING_APPROVAL', 'DISPATCHING', 'CANCELLED'},
    'AWAITING_APPROVAL': {'QUEUED', 'CANCELLED'},
    'DISPATCHING': {'RUNNING', 'RETRY_PENDING', 'FAILED', 'UNKNOWN'},
    'RETRY_PENDING': {'DISPATCHING', 'CANCELLED', 'UNKNOWN'},
    'RUNNING': {'SUCCEEDED', 'FAILED', 'UNKNOWN', 'CANCELLED'},
    'UNKNOWN': {'RUNNING', 'FAILED', 'RETRY_PENDING', 'CANCELLED'},
}


def fingerprint(tenant, principal, operation, payload):
    rules = json.loads((Path(__file__).resolve().parents[5] /
                        'packages/contracts/conventions/idempotency.json').read_text())
    rule = rules['operations'][operation]
    projected = {k: v for k, v in payload.items() if k not in rule['exclude_fields']}
    for field in rule['uuid_fields']:
        if field in projected:
            projected[field] = projected[field].lower()
    return hashlib.sha256(rfc8785.dumps({
        'profile': rules['fingerprint_profile'], 'tenant_id': str(tenant).lower(),
        'principal_id': str(principal).lower(), 'operation': operation, 'payload': projected,
    })).hexdigest()


class Ledger:
    def __init__(self, database_url):
        self.database_url = database_url

    def connect(self):
        return psycopg.connect(self.database_url, row_factory=dict_row)

    def create_task(self, tenant, principal, request, *, connection=None):
        if connection is None:
            with self.connect() as conn:
                return self.create_task(tenant, principal, request, connection=conn)
        conn = connection
        key = request['idempotency_key']
        digest = fingerprint(tenant, principal, 'task.create', request)
        # Transaction-scoped serialization covers absent rows as well as existing ones.
        lock = f'{tenant}:{principal}:create_task:{key}'
        conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (lock,))
        existing = conn.execute(
            'SELECT fingerprint,resource_id FROM idempotency_records '
            'WHERE tenant_id=%s AND principal_id=%s AND operation=%s AND key=%s',
            (tenant, principal, 'task.create', key),
        ).fetchone()
        if existing:
            if existing['fingerprint'] != digest:
                raise Conflict('idempotency_conflict')
            return self.get_task(tenant, principal, existing['resource_id'], connection=conn), False
        task_id = uuid4()
        conn.execute(
            'INSERT INTO tasks(id,tenant_id,principal_id,source_event_id,intent,objective,request,state) '
            "VALUES(%s,%s,%s,%s,%s,%s,%s,'QUEUED')",
            (task_id, tenant, principal, request.get('source_event_id'), request['intent'],
             request['objective'], Jsonb(request)),
        )
        conn.execute('INSERT INTO idempotency_records '
                     '(tenant_id,principal_id,operation,key,fingerprint,resource_id) VALUES(%s,%s,%s,%s,%s,%s)',
                     (tenant, principal, 'task.create', key, digest, task_id))
        self.enqueue(conn, task_id, 'DISPATCH', f'dispatch:{task_id}')
        conn.execute('INSERT INTO task_transitions(task_id,next_state,actor) VALUES(%s,%s,%s)',
                     (task_id, 'QUEUED', 'intake'))
        conn.execute('INSERT INTO audit_events(tenant_id,principal_id,task_id,action) VALUES(%s,%s,%s,%s)',
                     (tenant, principal, task_id, 'task.created'))
        return self.get_task(tenant, principal, task_id, connection=conn), True

    def get_task(self, tenant, principal, task_id, *, connection=None):
        if connection is None:
            with self.connect() as conn:
                return self.get_task(tenant, principal, task_id, connection=conn)
        row = connection.execute('SELECT * FROM tasks WHERE tenant_id=%s AND principal_id=%s AND id=%s',
                                 (tenant, principal, task_id)).fetchone()
        if row is None:
            raise NotFound('task_not_found')
        return row

    @staticmethod
    def enqueue(conn, task_id, kind, dedup_key, payload=None):
        conn.execute('INSERT INTO outbox(task_id,kind,dedup_key,payload) VALUES(%s,%s,%s,%s) '
                     'ON CONFLICT(dedup_key) DO NOTHING', (task_id, kind, dedup_key, Jsonb(payload or {})))

    @staticmethod
    def transition(conn, task_id, expected, target, actor, *, version=None):
        if target not in TRANSITIONS.get(expected, set()):
            raise Conflict('invalid_transition')
        row = conn.execute(
            'UPDATE tasks SET state=%s,version=version+1,updated_at=now() WHERE id=%s AND state=%s '
            'AND (%s::bigint IS NULL OR version=%s) RETURNING *',
            (target, task_id, expected, version, version),
        ).fetchone()
        if row is None:
            raise Conflict('stale_transition')
        conn.execute('INSERT INTO task_transitions(task_id,previous_state,next_state,actor) '
                     'VALUES(%s,%s,%s,%s)', (task_id, expected, target, actor))
        return row

    def claim(self, kinds, lease_seconds=30):
        with self.connect() as conn:
            token = uuid4()
            return conn.execute(
                "WITH candidate AS (SELECT id FROM outbox WHERE kind=ANY(%s) AND available_at<=now() "
                "AND (state='PENDING' OR (state='CLAIMED' AND lease_until<now())) "
                'ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1) '
                "UPDATE outbox SET state='CLAIMED',lease_token=%s,lease_until=now()+%s*interval '1 second', "
                'attempts=attempts+1 FROM candidate WHERE outbox.id=candidate.id RETURNING outbox.*',
                (kinds, token, lease_seconds),
            ).fetchone()

    @staticmethod
    def finish(conn, job):
        row = conn.execute("UPDATE outbox SET state='DONE',lease_until=NULL WHERE id=%s "
                           "AND state='CLAIMED' AND lease_token=%s AND lease_until>now() RETURNING id",
                           (job['id'], job['lease_token'])).fetchone()
        if row is None:
            raise Conflict('stale_lease')

    def ingest_event(self, tenant, principal, event, request):
        """Identity must already be verified. One intended intake task per external event."""
        if str(tenant).lower() != event['tenant_id'].lower():
            raise Conflict('identity_mismatch')
        digest = fingerprint(tenant, principal, 'event.ingest', event)
        with self.connect() as conn:
            lock = f"event:{event['platform']}:{event['platform_event_id']}"
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (lock,))
            existing = conn.execute('SELECT * FROM channel_events WHERE platform=%s AND external_event_id=%s',
                                    (event['platform'], event['platform_event_id'])).fetchone()
            if existing:
                if (existing['tenant_id'] != tenant or existing['principal_id'] != principal
                        or existing['fingerprint'] != digest):
                    raise Conflict('event_conflict')
                task = conn.execute('SELECT * FROM tasks WHERE source_event_id=%s',
                                    (existing['id'],)).fetchone()
                return task, False
            event_id = uuid4()
            conn.execute('INSERT INTO channel_events '
                         '(id,tenant_id,principal_id,platform,external_event_id,fingerprint,payload) '
                         'VALUES(%s,%s,%s,%s,%s,%s,%s)',
                         (event_id, tenant, principal, event['platform'], event['platform_event_id'],
                          digest, Jsonb(event)))
            internal_request = {**request, 'source_event_id': str(event_id),
                                'idempotency_key': f'event:{event_id}'}
            return self.create_task(tenant, principal, internal_request, connection=conn)
