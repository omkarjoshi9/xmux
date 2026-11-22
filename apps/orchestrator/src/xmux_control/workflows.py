"""Bounded DAGs. Parent worker invokes advance after terminal task updates."""
import hashlib
from uuid import uuid4

import rfc8785
from jsonschema import ValidationError
from psycopg.types.json import Jsonb

from xmux_control.approvals import Approvals, requires_approval
from xmux_control.contracts import validate as validate_contract
from xmux_control.db.ledger import Conflict, NotFound


def validate(steps):
    if not isinstance(steps, list) or not 2 <= len(steps) <= 3:
        raise ValueError('workflow_size')
    for step in steps:
        if not isinstance(step, dict):
            raise TypeError('invalid_step')
        if not isinstance(step.get('depends_on', []), list) or any(not isinstance(d, str) for d in step.get('depends_on', [])):
            raise ValueError('invalid_dependency')
        if set(step) - {'name', 'request', 'depends_on'} or not isinstance(step.get('name'), str) or not step['name']:
            raise ValueError('invalid_step')
        try:
            validate_contract('TaskRequest', step.get('request'))
        except ValidationError as exc:
            raise ValueError('invalid_task_request') from exc
    names = [s['name'] for s in steps]
    if len(set(names)) != len(names):
        raise ValueError('duplicate_step')
    done = set()
    for _ in steps:
        for step in steps:
            deps = set(step.get('depends_on', []))
            if not deps <= set(names) or step['name'] in deps:
                raise ValueError('invalid_dependency')
            if deps <= done:
                done.add(step['name'])
    if done != set(names):
        raise ValueError('workflow_cycle')


class Workflows:
    def __init__(self, ledger):
        self.ledger = ledger

    def create(self, actor, steps, idempotency_key):
        actor.require('tasks:create')
        validate(steps)
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200:
            raise ValueError('invalid_idempotency_key')
        digest = hashlib.sha256(rfc8785.dumps(steps)).hexdigest()
        workflow_id = uuid4()
        with self.ledger.connect() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                         (f'workflow:{actor.tenant}:{actor.principal}:{idempotency_key}',))
            existing = conn.execute('SELECT * FROM workflows WHERE tenant_id=%s AND principal_id=%s AND creation_key=%s',
                                    (actor.tenant, actor.principal, idempotency_key)).fetchone()
            if existing:
                if existing['creation_digest'] != digest:
                    raise Conflict('idempotency_conflict')
                return existing['id']
            conn.execute('INSERT INTO workflows(id,tenant_id,principal_id,creation_key,creation_digest) VALUES(%s,%s,%s,%s,%s)',
                         (workflow_id, actor.tenant, actor.principal, idempotency_key, digest))
            tasks = {}
            for step in steps:
                request = {**step['request'], 'idempotency_key': f'{workflow_id}:{step["name"]}'}
                task, _ = self.ledger.create_task(actor.tenant, actor.principal, request, connection=conn)
                tasks[step['name']] = task['id']
                conn.execute('UPDATE tasks SET workflow_id=%s WHERE id=%s', (workflow_id, task['id']))
                conn.execute('DELETE FROM outbox WHERE task_id=%s', (task['id'],))
            for step in steps:
                for dep in step.get('depends_on', []):
                    conn.execute('INSERT INTO workflow_dependencies VALUES(%s,%s)', (tasks[step['name']], tasks[dep]))
            self._advance(conn, workflow_id)
        return workflow_id

    def tick(self):
        # Workflow locks always precede task locks; sweep after worker transactions close.
        with self.ledger.connect() as conn:
            rows = conn.execute("SELECT id FROM workflows WHERE state='RUNNING'").fetchall()
        for row in rows:
            with self.ledger.connect() as conn:
                conn.execute('SELECT id FROM workflows WHERE id=%s FOR UPDATE', (row['id'],))
                self._advance(conn, row['id'])
        return False

    def advance(self, actor, workflow_id):
        actor.require('tasks:read')
        with self.ledger.connect() as conn:
            workflow = conn.execute('SELECT * FROM workflows WHERE id=%s AND tenant_id=%s AND principal_id=%s FOR UPDATE',
                                    (workflow_id, actor.tenant, actor.principal)).fetchone()
            if not workflow:
                raise NotFound('workflow_not_found')
            return self._advance(conn, workflow_id)

    def _advance(self, conn, workflow_id):
        for _ in range(3):
            tasks = conn.execute('SELECT * FROM tasks WHERE workflow_id=%s ORDER BY id FOR UPDATE', (workflow_id,)).fetchall()
            by_id = {t['id']: t for t in tasks}
            for task in tasks:
                if task['workflow_started'] or task['state'] != 'QUEUED':
                    continue
                deps = conn.execute('SELECT depends_on FROM workflow_dependencies WHERE task_id=%s', (task['id'],)).fetchall()
                upstream = [by_id[d['depends_on']] for d in deps]
                if any(t['state'] in {'FAILED', 'CANCELLED', 'REJECTED'} for t in upstream):
                    self.ledger.transition(conn, task['id'], 'QUEUED', 'CANCELLED', 'workflow')
                elif all(t['state'] == 'SUCCEEDED' for t in upstream):
                    refs = list(task['request'].get('context_refs', []))
                    for parent in upstream:
                        refs.append(f"task-result:{parent['id']}")
                    task['request'] = {**task['request'], 'context_refs': refs}
                    conn.execute('UPDATE tasks SET request=%s WHERE id=%s', (Jsonb(task['request']), task['id']))
                    conn.execute('UPDATE tasks SET workflow_started=true WHERE id=%s', (task['id'],))
                    if requires_approval(task['request']):
                        Approvals(self.ledger).gate(conn, task)
                    else:
                        self.ledger.enqueue(conn, task['id'], 'DISPATCH', f"workflow:{task['id']}")
        states = [t['state'] for t in conn.execute('SELECT state FROM tasks WHERE workflow_id=%s', (workflow_id,))]
        state = 'SUCCEEDED' if all(s == 'SUCCEEDED' for s in states) else (
            'FAILED' if all(s in {'SUCCEEDED', 'FAILED', 'CANCELLED', 'REJECTED'} for s in states) else 'RUNNING')
        conn.execute('UPDATE workflows SET state=%s WHERE id=%s', (state, workflow_id))
        return state
