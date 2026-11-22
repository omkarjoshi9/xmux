import hashlib
import json
from dataclasses import replace
from uuid import uuid4

from fastapi.testclient import TestClient
from xmux_control.api import create_app
from xmux_control.contracts import validate
from xmux_control.settings import Settings


def client_for(owner, tmp_path, role='user', principal=None):
    ledger, tenant, owner_id = owner
    principal = principal or owner_id
    token = 'test-' + uuid4().hex
    registry = tmp_path / (uuid4().hex + '.json')
    registry.write_text(json.dumps({'tokens': [{'sha256': hashlib.sha256(token.encode()).hexdigest(),
        'tenant_id': str(tenant), 'principal_id': str(principal), 'role': role,
        'scopes': ['tasks:create', 'tasks:read', 'tasks:cancel', 'events:ingest']}]}))
    settings = Settings(ledger.database_url, 'redis://localhost:6379', auth_registry_file=str(registry),
                        cursor_secret='test-only-cursor-key')
    return TestClient(create_app(settings)), {'Authorization': 'Bearer ' + token}


def test_authenticated_intake_replay_status_and_cancel(owner, task_request, tmp_path):
    client, headers = client_for(owner, tmp_path)
    assert client.post('/v1/tasks', json=task_request).status_code == 401
    response = client.post('/v1/tasks', json=task_request, headers=headers)
    assert response.status_code == 201
    validate('TaskReceipt', response.json())
    assert client.post('/v1/tasks', json=task_request, headers=headers).status_code == 200
    task_id = response.json()['task_id']
    response = client.get('/v1/tasks/' + task_id, headers=headers)
    validate('TaskStatus', response.json())
    result = client.get('/v1/tasks/' + task_id + '/result', headers=headers)
    assert result.status_code == 409
    validate('ErrorEnvelope', result.json())
    assert client.post('/v1/tasks/' + task_id + '/cancel', headers=headers).json()['status'] == 'CANCELLED'
    other, other_headers = client_for(owner, tmp_path, principal=uuid4())
    assert other.get('/v1/tasks/' + task_id, headers=other_headers).status_code == 404


def test_reject_spoofed_owner_bad_input_and_oversized_body(owner, task_request, tmp_path):
    client, headers = client_for(owner, tmp_path)
    for data in ({**task_request, 'principal_id': str(uuid4())}, {**task_request, 'schema_version': '99'}):
        response = client.post('/v1/tasks', json=data, headers=headers)
        assert response.status_code == 422
        validate('ErrorEnvelope', response.json())
    response = client.post('/v1/tasks', content='a' * 262145, headers=headers)
    assert response.status_code == 413
    validate('ErrorEnvelope', response.json())
    response = client.get('/v1/tasks/not-a-uuid', headers=headers)
    validate('ErrorEnvelope', response.json())
    assert response.status_code == 422


def test_pagination_is_signed_and_owner_bound(owner, task_request, tmp_path):
    client, headers = client_for(owner, tmp_path)
    for _ in range(3):
        client.post('/v1/tasks', json={**task_request, 'idempotency_key': str(uuid4())}, headers=headers)
    page = client.get('/v1/tasks?limit=2', headers=headers).json()
    validate('TaskPage', page)
    assert len(page['items']) == 2
    next_page = client.get('/v1/tasks', params={'cursor': page['next_cursor'], 'limit': 2}, headers=headers)
    assert len(next_page.json()['items']) == 1
    other, other_headers = client_for(owner, tmp_path, principal=uuid4())
    assert other.get('/v1/tasks', params={'cursor': page['next_cursor']}, headers=other_headers).status_code == 422
    assert client.get('/v1/tasks?cursor=bad', headers=headers).status_code == 422


def test_gateway_identity_and_event_replay(owner, tmp_path):
    ledger, tenant, principal = owner
    mapping = uuid4()
    with ledger.connect() as conn:
        conn.execute('INSERT INTO channel_identities VALUES(%s,%s,%s,%s,%s,true)',
                     (mapping, tenant, principal, 'telegram', 'user-test'))
    client, headers = client_for(owner, tmp_path, role='gateway')
    event = {'schema_version': '1', 'event_id': str(uuid4()), 'tenant_id': str(tenant),
             'platform': 'telegram', 'platform_event_id': str(uuid4()), 'event_type': 'direct_message',
             'occurred_at': '2026-11-21T12:00:00Z', 'actor_external_id': 'user-test',
             'text': '/research Evaluate this claim', 'urls': [], 'attachments': [],
             'reply_target': {'channel_id': 'test'}, 'auth_context': {'identity_mapping_ref': str(mapping)}}
    first = client.post('/v1/events', json=event, headers=headers)
    assert first.status_code == 201, first.text
    validate('EventReceipt', first.json())
    second = client.post('/v1/events', json={**event, 'event_id': str(uuid4())}, headers=headers)
    assert second.json() == first.json()
    assert second.status_code == 200
    spoofed = {**event, 'actor_external_id': 'unlinked'}
    assert client.post('/v1/events', json=spoofed, headers=headers).status_code == 403


def test_production_rejects_development_user_token(owner, tmp_path):
    client, headers = client_for(owner, tmp_path)
    production = TestClient(create_app(replace(client.app.state.settings, environment='production')))
    assert production.get('/v1/tasks', headers=headers).status_code == 401


def test_jwt_issuer_audience_expiry_and_server_scopes(owner, tmp_path):
    import time

    import jwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    ledger, tenant, principal = owner
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = tmp_path / 'public.pem'
    public.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,
                                                    serialization.PublicFormat.SubjectPublicKeyInfo))
    registry = tmp_path / 'subjects.json'
    registry.write_text(json.dumps({'subjects': {'user-1': {'tenant_id': str(tenant),
        'principal_id': str(principal), 'scopes': ['tasks:read']}}}))
    settings = Settings(ledger.database_url, 'redis://localhost:6379', environment='production',
                        auth_registry_file=str(registry), jwt_issuer='https://issuer.test',
                        jwt_public_key_file=str(public))
    client = TestClient(create_app(settings))
    claims = {'sub': 'user-1', 'iss': settings.jwt_issuer, 'aud': settings.jwt_audience,
              'iat': int(time.time()), 'exp': int(time.time())+60, 'scope': 'tasks:read tasks:create'}
    for changed, expected in (({}, 200), ({'aud': 'another-api'}, 401),
                              ({'iss': 'https://wrong.test'}, 401), ({'exp': 1}, 401)):
        token = jwt.encode({**claims, **changed}, key, algorithm='RS256')
        assert client.get('/v1/tasks', headers={'Authorization': 'Bearer '+token}).status_code == expected
    token = jwt.encode(claims, key, algorithm='RS256')
    assert client.post('/v1/tasks', json={}, headers={'Authorization': 'Bearer '+token}).status_code == 403
