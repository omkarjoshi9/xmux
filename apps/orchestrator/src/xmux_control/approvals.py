"""Exact-action approval gates. Call gate inside the task creation transaction."""
import hashlib
from uuid import uuid4

import rfc8785

from xmux_control.db.ledger import Conflict, NotFound


def action_digest(task):
    return hashlib.sha256(rfc8785.dumps({
        'tenant': str(task['tenant_id']), 'principal': str(task['principal_id']),
        'task': str(task['id']), 'request': task['request'],
    })).hexdigest()


def requires_approval(request):
    # Unknown intents fail closed; captured content never grants permission.
    return request.get('intent') not in {'research', 'summarize'} or bool(request.get('constraints'))


class Approvals:
    def __init__(self, ledger):
        self.ledger = ledger

    def gate(self, conn, task, ttl_seconds=3600):
        if not 0 < ttl_seconds <= 86400:
            raise ValueError('invalid_expiry')
        self.ledger.transition(conn, task['id'], 'QUEUED', 'AWAITING_APPROVAL', 'policy')
        conn.execute("DELETE FROM outbox WHERE task_id=%s AND kind='DISPATCH'", (task['id'],))
        approval_id = uuid4()
        conn.execute("INSERT INTO approvals(id,task_id,action_digest,state,expires_at) "
                     "VALUES(%s,%s,%s,'PENDING',now()+%s*interval '1 second')",
                     (approval_id, task['id'], action_digest(task), ttl_seconds))
        return approval_id

    def decide(self, actor, approval_id, digest, decision):
        actor.require('approvals:decide')
        if decision not in {'approve', 'deny'}:
            raise ValueError('invalid_decision')
        with self.ledger.connect() as conn:
            workflow = conn.execute('SELECT t.workflow_id FROM approvals a JOIN tasks t ON t.id=a.task_id WHERE a.id=%s AND t.tenant_id=%s AND t.principal_id=%s', (approval_id, actor.tenant, actor.principal)).fetchone()
            if workflow and workflow['workflow_id']:
                conn.execute('SELECT id FROM workflows WHERE id=%s FOR UPDATE', (workflow['workflow_id'],))
            row = conn.execute('SELECT a.*,t.tenant_id,t.principal_id,t.request,t.state AS task_state '
                               'FROM approvals a JOIN tasks t ON t.id=a.task_id '
                               'WHERE a.id=%s AND t.tenant_id=%s AND t.principal_id=%s FOR UPDATE OF a,t',
                               (approval_id, actor.tenant, actor.principal)).fetchone()
            if not row:
                raise NotFound('approval_not_found')
            task = self.ledger.get_task(actor.tenant, actor.principal, row['task_id'], connection=conn)
            if digest != row['action_digest'] or digest != action_digest(task):
                raise Conflict('action_changed')
            if row['state'] != 'PENDING' or row['task_state'] != 'AWAITING_APPROVAL':
                raise Conflict('approval_consumed')
            expired = conn.execute('SELECT %s<=now() AS expired', (row['expires_at'],)).fetchone()['expired']
            state = 'EXPIRED' if expired else ('APPROVED' if decision == 'approve' else 'DENIED')
            conn.execute('UPDATE approvals SET state=%s,decided_at=now(),decided_by=%s WHERE id=%s',
                         (state, actor.principal, approval_id))
            target = 'QUEUED' if state == 'APPROVED' else 'CANCELLED'
            self.ledger.transition(conn, task['id'], 'AWAITING_APPROVAL', target, str(actor.principal))
            if target == 'QUEUED':
                self.ledger.enqueue(conn, task['id'], 'DISPATCH', f"approved:{approval_id}")
            conn.execute('INSERT INTO audit_events(tenant_id,principal_id,task_id,action) VALUES(%s,%s,%s,%s)',
                         (actor.tenant, actor.principal, task['id'], 'approval.' + state.lower()))
            if task['workflow_id']:
                from xmux_control.workflows import Workflows
                Workflows(self.ledger)._advance(conn, task['workflow_id'])
            return state

    def expire(self):
        """Worker sweep; decisions and propagation use the same row locks."""
        from xmux_control.auth import Actor
        with self.ledger.connect() as conn:
            rows = conn.execute("SELECT a.id,a.action_digest,t.tenant_id,t.principal_id FROM approvals a JOIN tasks t ON t.id=a.task_id WHERE a.state='PENDING' AND a.expires_at<=now() LIMIT 100").fetchall()
        for row in rows:
            actor = Actor(row['tenant_id'], row['principal_id'], frozenset({'approvals:decide'}), 'user')
            try:
                self.decide(actor, row['id'], row['action_digest'], 'deny')
            except Conflict:
                continue
        return len(rows)
