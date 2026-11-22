import json
import time
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from starlette.testclient import TestClient
from xmux_mcp.app import SCOPES, Config, create_app


@pytest.fixture
def setup(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = tmp_path / 'key.pem'
    public.write_bytes(key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo))
    accounts = tmp_path / 'accounts.json'
    accounts.write_text(json.dumps({'alice': {'tenant_id': str(uuid4()), 'principal_id': str(uuid4()),
        'scopes': list(set(SCOPES.values())), 'api_access_token': 'separate-api-token'}}))
    config = Config('https://issuer.example', 'http://localhost/mcp', str(public), str(accounts), 'http://localhost')
    seen = []
    def upstream(request):
        assert request.headers['authorization'] == 'Bearer separate-api-token'
        if request.url.path == '/v1/identity':
            return httpx.Response(200, json=json.loads(accounts.read_text())['alice'])
        seen.append(request)
        if str(request.url).endswith('/00000000-0000-0000-0000-000000000000'):
            return httpx.Response(404, json={'secret': 'other owner'})
        return httpx.Response(200, json={'ok': True})
    def token(**changes):
        claims = dict(iss=config.issuer, aud=config.resource, sub='alice', iat=int(time.time()),
                      exp=int(time.time())+300, scope=' '.join(set(SCOPES.values())))
        claims.update(changes)
        return jwt.encode(claims, key, algorithm='RS256')
    with TestClient(create_app(config, httpx.MockTransport(upstream)), base_url='http://localhost') as client:
        yield client, token, seen


def rpc(client, token, method, params=None):
    return client.post('/mcp', headers={'Authorization': 'Bearer '+token,
        'Accept': 'application/json, text/event-stream'}, json={'jsonrpc': '2.0', 'id': 1,
        'method': method, 'params': params or {}})


def test_auth_and_discovery(setup):
    client, token, seen = setup
    response = client.post('/mcp')
    assert response.status_code == 401
    assert 'resource_metadata' in response.headers['www-authenticate']
    assert client.get('/.well-known/oauth-protected-resource/mcp').json()['resource'] == 'http://localhost/mcp'
    for changes in ({'aud': 'wrong'}, {'iss': 'wrong'}, {'exp': 1}, {'sub': 'unlinked'}):
        assert rpc(client, token(**changes), 'tools/list').status_code == 401
    assert not seen


def test_protocol_tools_scope_and_owner(setup):
    client, token, seen = setup
    init = rpc(client, token(), 'initialize', {'protocolVersion': '2025-11-25', 'capabilities': {},
                                            'clientInfo': {'name': 'test', 'version': '1'}})
    assert init.status_code == 200 and 'protocolVersion' in init.json()['result']
    tools = rpc(client, token(), 'tools/list').json()['result']['tools']
    assert {t['name'] for t in tools} == set(SCOPES)
    denied = rpc(client, token(scope=''), 'tools/call', {'name': 'list_agents', 'arguments': {}})
    assert denied.json()['result']['isError'] and not seen
    task = dict(schema_version='1', idempotency_key='stable', intent='delegate', objective='test',
                context_refs=[], requested_agent='agent:test')
    arguments = {'capture_content': {'request': task}, 'create_task': {'request': task},
        'delegate_task': {'request': task}, 'get_task_status': {'task_id': str(uuid4())},
        'get_result': {'task_id': str(uuid4())}, 'list_agents': {}, 'approve_action': {
        'approval_id': str(uuid4()), 'decision': {'decision': 'approve'}}, 'list_routines': {},
        'create_routine': {'request': {}}, 'search_references': {'query': 'test'}}
    for name, args in arguments.items():
        response = rpc(client, token(), 'tools/call', {'name': name, 'arguments': args})
        assert not response.json()['result']['isError'], response.text
    assert [json.loads(r.content) for r in seen[:3]] == [task]*3
    response = rpc(client, token(), 'tools/call', {'name': 'get_task_status',
        'arguments': {'task_id': '00000000-0000-0000-0000-000000000000'}})
    assert response.json()['result']['isError']
    assert 'other owner' not in response.text
