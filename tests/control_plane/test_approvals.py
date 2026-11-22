import pytest
from xmux_control.approvals import Approvals, action_digest
from xmux_control.auth import Actor
from xmux_control.db.ledger import Conflict


def test_gate_and_replay(owner, task_request):
    ledger, tenant, principal = owner
    actor = Actor(tenant, principal, frozenset({'approvals:decide'}), 'user')
    service = Approvals(ledger)
    with ledger.connect() as conn:
        task, _ = ledger.create_task(tenant, principal, task_request, connection=conn)
        approval = service.gate(conn, task)
        assert conn.execute('SELECT count(*) AS n FROM outbox WHERE task_id=%s', (task['id'],)).fetchone()['n'] == 0
    assert service.decide(actor, approval, action_digest(task), 'approve') == 'APPROVED'
    with pytest.raises(Conflict):
        service.decide(actor, approval, action_digest(task), 'approve')


@pytest.mark.parametrize('decision,expired,expected', [('deny', False, 'DENIED'), ('approve', True, 'EXPIRED')])
def test_denial_expiry(owner, task_request, decision, expired, expected):
    ledger, tenant, principal = owner
    actor = Actor(tenant, principal, frozenset({'approvals:decide'}), 'user')
    service = Approvals(ledger)
    with ledger.connect() as conn:
        task, _ = ledger.create_task(tenant, principal, task_request, connection=conn)
        approval = service.gate(conn, task)
        if expired:
            conn.execute("UPDATE approvals SET expires_at=now()-interval '1 second' WHERE id=%s", (approval,))
    assert service.decide(actor, approval, action_digest(task), decision) == expected
    assert ledger.get_task(tenant, principal, task['id'])['state'] == 'CANCELLED'


def test_changed_action_and_wrong_owner(owner, task_request):
    from uuid import uuid4

    from xmux_control.db.ledger import NotFound
    ledger, tenant, principal = owner
    actor = Actor(tenant, principal, frozenset({'approvals:decide'}), 'user')
    service = Approvals(ledger)
    with ledger.connect() as conn:
        task, _ = ledger.create_task(tenant, principal, task_request, connection=conn)
        approval = service.gate(conn, task)
    with pytest.raises(NotFound):
        service.decide(Actor(tenant, uuid4(), actor.scopes, 'user'), approval, action_digest(task), 'approve')
    with pytest.raises(Conflict):
        service.decide(actor, approval, '0' * 64, 'approve')


def test_concurrent_decisions_dispatch_once(owner, task_request):
    from concurrent.futures import ThreadPoolExecutor
    ledger, tenant, principal = owner
    actor = Actor(tenant, principal, frozenset({'approvals:decide'}), 'user')
    service = Approvals(ledger)
    with ledger.connect() as conn:
        task, _ = ledger.create_task(tenant, principal, task_request, connection=conn)
        identity = service.gate(conn, task)
    def decide(_):
        try:
            return service.decide(actor, identity, action_digest(task), 'approve')
        except Conflict:
            return 'CONFLICT'
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(decide, range(2))) == ['APPROVED', 'CONFLICT']
    with ledger.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM outbox WHERE task_id=%s', (task['id'],)).fetchone()['n'] == 1
