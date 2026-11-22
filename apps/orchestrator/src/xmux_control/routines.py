"""Remote owns execution. Adapter methods receive authenticated owner on every call."""
import hashlib
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import rfc8785
from psycopg.types.json import Jsonb

from xmux_control.db.ledger import Conflict, NotFound


class Routines:
    def __init__(self, ledger, remote):
        self.ledger, self.remote = ledger, remote

    def create(self, actor, key, agent, request):
        actor.require('routines:create')
        if not isinstance(key, str) or not key.strip() or len(key) > 255 or not isinstance(agent, str) or not agent.strip():
            raise ValueError('invalid_routine_identity')
        if not isinstance(request, dict) or set(request) != {'timezone', 'recurrence'} or not all(isinstance(v, str) and v.strip() for v in request.values()):
            raise ValueError('invalid_schedule')
        try:
            ZoneInfo(request['timezone'])
        except ZoneInfoNotFoundError as exc:
            raise ValueError('invalid_timezone') from exc
        if not request.get('recurrence'):
            raise ValueError('recurrence_required')
        capabilities = self.remote.capabilities(actor, agent)
        if not capabilities.get('schedule_create') or not capabilities.get('authorized'):
            raise Conflict('scheduling_unsupported_or_forbidden')
        digest = hashlib.sha256(rfc8785.dumps({'agent': agent, 'request': request})).hexdigest()
        with self.ledger.connect() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                         (f'routine:{actor.tenant}:{actor.principal}:{key}',))
            existing = conn.execute('SELECT * FROM routine_requests WHERE tenant_id=%s AND principal_id=%s AND key=%s',
                                    (actor.tenant, actor.principal, key)).fetchone()
            if existing:
                if existing['digest'] != digest:
                    raise Conflict('idempotency_conflict')
                return existing
            identity = uuid4()
            conn.execute('INSERT INTO routine_requests(id,tenant_id,principal_id,key,digest,agent_id,request) '
                         'VALUES(%s,%s,%s,%s,%s,%s,%s)',
                         (identity, actor.tenant, actor.principal, key, digest, agent, Jsonb(request)))
        return self.get(actor, identity)

    def decide(self, actor, identity, digest, decision):
        actor.require('approvals:decide')
        if decision not in {'approve', 'deny'}:
            raise ValueError('invalid_decision')
        with self.ledger.connect() as conn:
            row = conn.execute('SELECT * FROM routine_requests WHERE id=%s AND tenant_id=%s AND principal_id=%s FOR UPDATE', (identity, actor.tenant, actor.principal)).fetchone()
            if not row:
                raise NotFound('routine_not_found')
            actual = hashlib.sha256(rfc8785.dumps({'agent': row['agent_id'], 'request': row['request']})).hexdigest()
            if digest != row['digest'] or digest != actual:
                raise Conflict('action_changed')
            if row['state'] != 'PENDING':
                raise Conflict('approval_consumed')
            expired = conn.execute('SELECT %s<=now() AS expired', (row['expires_at'],)).fetchone()['expired']
            state = 'EXPIRED' if expired else ('DENIED' if decision == 'deny' else 'UNKNOWN')
            conn.execute('UPDATE routine_requests SET state=%s,decided_by=%s,decided_at=now() WHERE id=%s', (state, actor.principal, identity))
        if state != 'UNKNOWN':
            return self.get(actor, identity)
        # UNKNOWN commits before I/O: crashes and acceptance timeouts never resubmit.
        capabilities = self.remote.capabilities(actor, row['agent_id'])
        if not capabilities.get('schedule_create') or not capabilities.get('authorized'):
            raise Conflict('scheduling_unsupported_or_forbidden')
        try:
            result = self.remote.create_schedule(actor, row['agent_id'], str(identity), row['request'])
        except (TimeoutError, ConnectionError):
            return self.get(actor, identity)
        remote_id = result.get('remote_schedule_id')
        if not isinstance(remote_id, str) or not remote_id.strip():
            raise Conflict('remote_schedule_id_required')
        with self.ledger.connect() as conn:
            conn.execute("UPDATE routine_requests SET remote_schedule_id=%s,state='ACTIVE',last_verified_at=now() WHERE id=%s",
                         (remote_id, identity))
        return self.get(actor, identity)

    def list(self, actor):
        actor.require('routines:read')
        with self.ledger.connect() as conn:
            return conn.execute('SELECT * FROM routine_requests WHERE tenant_id=%s AND principal_id=%s ORDER BY id LIMIT 100',
                                (actor.tenant, actor.principal)).fetchall()

    def get(self, actor, identity):
        actor.require('routines:read')
        with self.ledger.connect() as conn:
            row = conn.execute('SELECT * FROM routine_requests WHERE id=%s AND tenant_id=%s AND principal_id=%s',
                               (identity, actor.tenant, actor.principal)).fetchone()
        if not row:
            raise NotFound('routine_not_found')
        return row

    def reconcile(self, actor, identity):
        row = self.get(actor, identity)
        if row['state'] in {'PENDING', 'DENIED', 'EXPIRED'}:
            return row
        capabilities = self.remote.capabilities(actor, row['agent_id'])
        if not capabilities.get('schedule_lookup') or not capabilities.get('authorized'):
            raise Conflict('schedule_lookup_unsupported_or_forbidden')
        try:
            result = self.remote.lookup_schedule(actor, row['agent_id'], str(identity), row['remote_schedule_id'])
        except (TimeoutError, ConnectionError):
            result = {'state': 'UNKNOWN'}
        state = result.get('state', 'UNKNOWN')
        if state not in {'ACTIVE', 'REMOVED', 'UNKNOWN'}:
            raise Conflict('invalid_remote_state')
        remote_id = result.get('remote_schedule_id') or row['remote_schedule_id']
        if state == 'ACTIVE' and not remote_id:
            raise Conflict('remote_schedule_id_required')
        with self.ledger.connect() as conn:
            conn.execute('UPDATE routine_requests SET state=%s,remote_schedule_id=%s,last_verified_at=now() WHERE id=%s',
                         (state, remote_id, identity))
        return self.get(actor, identity)

    def expire(self):
        with self.ledger.connect() as conn:
            return conn.execute("UPDATE routine_requests SET state='EXPIRED',decided_at=now() WHERE state='PENDING' AND expires_at<=now()").rowcount
