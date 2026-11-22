from xmux_control.auth import Actor
from xmux_control.routines import Routines


class Remote:
    calls = 0
    def capabilities(self, actor, agent):
        return {'authorized': True, 'schedule_create': True, 'schedule_lookup': True}
    def create_schedule(self, *args):
        self.calls += 1
        raise TimeoutError()
    def lookup_schedule(self, *args):
        return {'state': 'ACTIVE', 'remote_schedule_id': 'remote-1'}


def test_ambiguous_creation_never_repeated(owner):
    ledger, tenant, principal = owner
    actor = Actor(tenant, principal, frozenset({'routines:create', 'routines:read', 'approvals:decide'}), 'user')
    remote = Remote()
    service = Routines(ledger, remote)
    request = {'timezone': 'UTC', 'recurrence': 'daily'}
    row = service.create(actor, 'key', 'agent', request)
    assert row['state'] == 'PENDING'
    assert remote.calls == 0
    row = service.decide(actor, row['id'], row['digest'], 'approve')
    assert row['state'] == 'UNKNOWN'
    service.create(actor, 'key', 'agent', request)
    assert remote.calls == 1
    assert service.reconcile(actor, row['id'])['state'] == 'ACTIVE'


def test_concurrent_approval_and_denial(owner):
    from concurrent.futures import ThreadPoolExecutor

    from xmux_control.db.ledger import Conflict
    ledger, tenant, principal = owner
    actor = Actor(tenant, principal, frozenset({'routines:create', 'routines:read', 'approvals:decide'}), 'user')
    remote = Remote()
    service = Routines(ledger, remote)
    row = service.create(actor, 'concurrent', 'agent', {'timezone': 'UTC', 'recurrence': 'daily'})
    def approve(_):
        try:
            return service.decide(actor, row['id'], row['digest'], 'approve')['state']
        except Conflict:
            return 'CONFLICT'
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(approve, range(2))) == ['CONFLICT', 'UNKNOWN']
    assert remote.calls == 1
    denied = service.create(actor, 'denied', 'agent', {'timezone': 'UTC', 'recurrence': 'daily'})
    assert service.decide(actor, denied['id'], denied['digest'], 'deny')['state'] == 'DENIED'
    assert service.reconcile(actor, denied['id'])['state'] == 'DENIED'
    assert remote.calls == 1


def test_router_rejects_invalid_inputs(owner):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from xmux_control.auth import authenticate
    from xmux_control.phase6_router import install as install_workflows
    from xmux_control.phase8_router import install as install_routines
    ledger, tenant, principal = owner
    app = FastAPI()
    app.dependency_overrides[authenticate] = lambda: Actor(tenant, principal, frozenset({'routines:create'}), 'user')
    install_routines(app, ledger, Remote())
    install_workflows(app, ledger)
    client = TestClient(app)
    assert client.post('/v1/routines', json={}).status_code == 422
    assert client.post('/v1/routines', json={'idempotency_key': 'k', 'agent_id': 'a', 'request': {'timezone': 'invalid', 'recurrence': 'daily'}}).status_code == 422
    assert client.post('/v1/workflows', json={'idempotency_key': 'k', 'steps': [{}, {}]}).status_code == 422


def test_http_schedule_rejects_cross_owner_result(owner, monkeypatch):
    import httpx
    import pytest
    from xmux_control.routine_adapter import HttpRoutines
    from xmux_control.settings import Settings
    ledger, tenant, principal = owner
    actor = Actor(tenant, principal, frozenset(), 'user')
    monkeypatch.setenv('MULTIPLEXER_SCHEDULE_CREATE_PATH', '/schedule')
    remote = HttpRoutines(Settings(ledger.database_url, 'redis://localhost', multiplexer_url='http://test'),
                          transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
                              'remote_schedule_id': 'another-user-schedule'})))
    with pytest.raises(ConnectionError, match='binding'):
        remote.create_schedule(actor, 'agent', 'correlation', {'timezone': 'UTC', 'recurrence': 'daily'})
