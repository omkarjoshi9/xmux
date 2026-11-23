from fastapi import FastAPI
from fastapi.testclient import TestClient
from xmux_control.operations import mount_operations


def test_metrics_require_auth_and_exclude_request_content():
    app = FastAPI()
    mount_operations(app, 'metrics-test-secret')
    client = TestClient(app)
    assert client.get('/internal/metrics').status_code == 401
    client.get('/private-objective-sentinel?token=secret-sentinel')
    response = client.get('/internal/metrics', headers={'Authorization': 'Bearer metrics-test-secret'})
    assert response.status_code == 200
    assert 'xmux_http_requests_total' in response.text
    assert 'sentinel' not in response.text
    assert response.headers['cache-control'] == 'no-store'


def test_global_limit_blocks_oversized_body_before_route():
    from starlette.requests import Request
    from xmux_control.operations import RequestLimits
    app = FastAPI()
    seen = []
    @app.post('/any-router')
    async def endpoint(request: Request):
        seen.append(await request.body())
        return {'ok': True}
    app.add_middleware(RequestLimits, maximum=10)
    client = TestClient(app)
    assert client.post('/any-router', content=b'x'*11).status_code == 413
    assert not seen
    assert client.post('/any-router', content=b'x'*10).status_code == 200
    assert seen == [b'x'*10]


def test_rate_limit_is_owner_scoped_and_fails_closed(monkeypatch):
    from uuid import uuid4

    import pytest
    from fastapi import HTTPException
    from redis.exceptions import ConnectionError
    from xmux_control import auth
    from xmux_control.settings import Settings
    calls = []
    class Client:
        def eval(self, script, keys, key):
            calls.append(key)
            return len(calls)
        def close(self):
            pass
    monkeypatch.setattr(auth.Redis, 'from_url', lambda *a, **kw: Client())
    actor = auth.Actor(uuid4(), uuid4(), frozenset(), 'user')
    settings = Settings('postgresql://test', 'redis://localhost', requests_per_minute=1)
    assert auth.rate_limit(settings, actor) == actor
    with pytest.raises(HTTPException) as error:
        auth.rate_limit(settings, actor)
    assert error.value.status_code == 429
    assert str(actor.principal) in calls[0]
    def unavailable(*a, **kw):
        raise ConnectionError()
    monkeypatch.setattr(auth.Redis, 'from_url', unavailable)
    with pytest.raises(HTTPException) as error:
        auth.rate_limit(settings, actor)
    assert error.value.status_code == 503
