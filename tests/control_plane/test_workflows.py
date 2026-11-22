import pytest
from xmux_control.auth import Actor
from xmux_control.workflows import Workflows, validate


def test_cycle():
    with pytest.raises(ValueError):
        validate([{'name': 'a', 'depends_on': ['b'], 'request': {}}, {'name': 'b', 'depends_on': ['a'], 'request': {}}])


def test_dependency_gate(owner, task_request):
    ledger, tenant, principal = owner
    actor = Actor(tenant, principal, frozenset({'tasks:create', 'tasks:read'}), 'user')
    service = Workflows(ledger)
    identity = service.create(actor, [{'name': 'a', 'request': task_request},
                                     {'name': 'b', 'depends_on': ['a'], 'request': {**task_request, 'intent': 'delegate'}}], 'workflow-key')
    service.advance(actor, identity)
    with ledger.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM outbox JOIN tasks ON tasks.id=outbox.task_id WHERE workflow_id=%s', (identity,)).fetchone()['n'] == 1


def test_upstream_cancel(owner, task_request):
    ledger, tenant, principal = owner
    actor = Actor(tenant, principal, frozenset({'tasks:create', 'tasks:read'}), 'user')
    service = Workflows(ledger)
    identity = service.create(actor, [{'name': 'a', 'request': task_request},
                                     {'name': 'b', 'depends_on': ['a'], 'request': task_request}], 'workflow-key')
    with ledger.connect() as conn:
        task = conn.execute('SELECT * FROM tasks WHERE workflow_id=%s AND workflow_started', (identity,)).fetchone()
        ledger.transition(conn, task['id'], 'QUEUED', 'CANCELLED', 'test')
    assert service.advance(actor, identity) == 'FAILED'
    with ledger.connect() as conn:
        assert all(row['state'] == 'CANCELLED' for row in conn.execute('SELECT state FROM tasks WHERE workflow_id=%s', (identity,)))


def test_atomic_replay_and_context_approval(owner, task_request):
    from concurrent.futures import ThreadPoolExecutor

    from xmux_control.approvals import Approvals, action_digest
    from xmux_control.db.ledger import Conflict
    ledger, tenant, principal = owner
    actor = Actor(tenant, principal, frozenset({'tasks:create', 'tasks:read', 'approvals:decide'}), 'user')
    service = Workflows(ledger)
    steps = [{'name': 'research', 'request': task_request},
             {'name': 'coding', 'depends_on': ['research'], 'request': {**task_request, 'intent': 'delegate'}}]
    with ThreadPoolExecutor(2) as pool:
        ids = list(pool.map(lambda _: service.create(actor, steps, 'same'), range(2)))
    assert ids[0] == ids[1]
    with pytest.raises(Conflict):
        service.create(actor, list(reversed(steps)), 'same')
    with ledger.connect() as conn:
        rows = conn.execute('SELECT * FROM tasks WHERE workflow_id=%s', (ids[0],)).fetchall()
        assert len(rows) == 2
        upstream = next(t for t in rows if t['workflow_started'])
        ledger.transition(conn, upstream['id'], 'QUEUED', 'DISPATCHING', 'test')
        ledger.transition(conn, upstream['id'], 'DISPATCHING', 'RUNNING', 'test')
        ledger.transition(conn, upstream['id'], 'RUNNING', 'SUCCEEDED', 'test')
    service.advance(actor, ids[0])
    with ledger.connect() as conn:
        downstream = conn.execute("SELECT * FROM tasks WHERE workflow_id=%s AND state='AWAITING_APPROVAL'", (ids[0],)).fetchone()
        assert downstream['request']['context_refs'] == [f"task-result:{upstream['id']}"]
        assert downstream['request']['objective'] == task_request['objective']
        assert conn.execute('SELECT count(*) AS n FROM outbox WHERE task_id=%s', (downstream['id'],)).fetchone()['n'] == 0
        approval = conn.execute('SELECT * FROM approvals WHERE task_id=%s', (downstream['id'],)).fetchone()
        assert approval['action_digest'] == action_digest(downstream)
        conn.execute("UPDATE approvals SET expires_at=now()-interval '1 second' WHERE id=%s", (approval['id'],))
    Approvals(ledger).expire()
    assert service.advance(actor, ids[0]) == 'FAILED'


def test_expiry_cancels_blocked_descendant(owner, task_request):
    from xmux_control.approvals import Approvals
    ledger, tenant, principal = owner
    actor = Actor(tenant, principal, frozenset({'tasks:create', 'tasks:read'}), 'user')
    identity = Workflows(ledger).create(actor, [
        {'name': 'a', 'request': {**task_request, 'intent': 'delegate'}},
        {'name': 'b', 'depends_on': ['a'], 'request': task_request}], 'expiry')
    with ledger.connect() as conn:
        conn.execute("UPDATE approvals SET expires_at=now()-interval '1 second' WHERE task_id IN (SELECT id FROM tasks WHERE workflow_id=%s)", (identity,))
    Approvals(ledger).expire()
    with ledger.connect() as conn:
        assert all(t['state'] == 'CANCELLED' for t in conn.execute('SELECT state FROM tasks WHERE workflow_id=%s', (identity,)))
