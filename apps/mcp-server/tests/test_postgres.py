"""Real MCP HTTP -> orchestrator ASGI -> isolated PostgreSQL database."""
import json
import time
from uuid import uuid4

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from starlette.testclient import TestClient
from test_protocol import rpc
from xmux_control.api import create_app as api_app
from xmux_control.catalog_router import install
from xmux_control.settings import Settings
from xmux_mcp.app import Config, create_app


def test_signed_ownership(owner, tmp_path):
    ledger, tenant, principal = owner
    scopes = ['tasks:create', 'tasks:read', 'references:read']
    keys = [rsa.generate_private_key(public_exponent=65537, key_size=2048) for _ in range(2)]
    for name, key in zip(('api', 'mcp'), keys):
        (tmp_path / name).write_bytes(key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo))
    def sign(index, subject, granted=scopes):
        return jwt.encode(dict(iss='https://issuer.example', aud=['api', 'http://localhost/mcp'][index],
            sub=subject, iat=int(time.time()), exp=int(time.time())+300, scope=' '.join(granted)),
            keys[index], algorithm='RS256')
    subjects = {'alice': dict(tenant_id=str(tenant), principal_id=str(principal), scopes=scopes),
                'bob': dict(tenant_id=str(tenant), principal_id=str(uuid4()), scopes=scopes)}
    registry = tmp_path / 'registry.json'
    registry.write_text(json.dumps({'subjects': subjects}))
    accounts = tmp_path / 'accounts.json'
    accounts.write_text(json.dumps({name: dict(data, api_access_token=sign(0, name)) for name, data in subjects.items()}))
    api = api_app(Settings(ledger.database_url, '', environment='production', auth_registry_file=str(registry),
        jwt_issuer='https://issuer.example', jwt_audience='api', jwt_public_key_file=str(tmp_path / 'api')))
    install(api, ledger)
    config = Config('https://issuer.example', 'http://localhost/mcp', str(tmp_path / 'mcp'), str(accounts), 'http://localhost')
    with TestClient(create_app(config, httpx.ASGITransport(app=api)), base_url='http://localhost') as client:
        def call(subject, name, arguments, granted=scopes):
            return rpc(client, sign(1, subject, granted), 'tools/call', {'name': name, 'arguments': arguments}).json()['result']
        request = dict(schema_version='1', idempotency_key=str(uuid4()), intent='research', objective='owned', context_refs=[])
        created = call('alice', 'create_task', {'request': request})
        assert not created['isError'], created
        task = json.loads(created['content'][0]['text'])
        task_id = task['task_id']
        assert not call('alice', 'get_task_status', {'task_id': task_id})['isError']
        assert call('bob', 'get_task_status', {'task_id': task_id})['isError']
        assert call('alice', 'get_task_status', {'task_id': task_id}, [])['isError']
        mapping = json.loads(accounts.read_text())
        mapping['alice']['api_access_token'] = sign(0, 'bob')
        accounts.write_text(json.dumps(mapping))
        assert 'upstream_identity_mismatch' in str(call('alice', 'get_task_status', {'task_id': task_id}))
