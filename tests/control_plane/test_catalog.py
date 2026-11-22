import json
from uuid import uuid4

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from xmux_control.auth import Actor, authenticate
from xmux_control.catalog_router import install
from xmux_control.contracts import ROOT


def test_registry_validates_boundary_and_propagates_trusted_identity(owner):
    ledger, tenant, principal = owner
    actor = Actor(tenant, principal, frozenset({'agents:read'}), 'user')
    replies = [json.loads((ROOT / 'fixtures/v1/agents.json').read_text()), {'private': 'sentinel'}]
    seen = []
    def upstream(request):
        seen.append(request)
        return httpx.Response(200, json=replies.pop(0))
    app = FastAPI()
    app.dependency_overrides[authenticate] = lambda: actor
    install(app, ledger, registry_url='https://registry.test/v1/agents', registry_token='test-service',
            transport=httpx.MockTransport(upstream))
    client = TestClient(app)
    assert client.get('/v1/agents').status_code == 200
    assert seen[0].headers['X-Xmux-Principal'] == str(principal)
    denied = client.get('/v1/agents')
    assert denied.status_code == 503
    assert 'sentinel' not in denied.text


def test_reference_search_is_owner_filtered(owner, task_request):
    ledger, tenant, principal = owner
    ledger.create_task(tenant, principal, {**task_request, 'context_refs': ['private-reference:test']})
    app = FastAPI()
    install(app, ledger)
    app.dependency_overrides[authenticate] = lambda: Actor(
        tenant, principal, frozenset({'references:read'}), 'user')
    client = TestClient(app)
    assert len(client.get('/v1/references/search?query=private').json()['items']) == 1
    app.dependency_overrides[authenticate] = lambda: Actor(
        tenant, uuid4(), frozenset({'references:read'}), 'user')
    assert client.get('/v1/references/search?query=private').json()['items'] == []
