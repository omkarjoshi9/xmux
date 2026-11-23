import hashlib
import hmac
import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import jwt
from fastapi import HTTPException, Request
from redis import Redis
from redis.exceptions import RedisError

from xmux_control.contracts import ROOT


@dataclass(frozen=True)
class Actor:
    tenant: UUID
    principal: UUID
    scopes: frozenset[str]
    role: str

    def require(self, scope):
        rules = json.loads((ROOT / 'conventions/authorization.json').read_text())['operations']
        rule = next((r for r in rules.values() if r['scope'] == scope), None)
        allowed_role = rule and (('USER' in rule['actor_types']) if self.role == 'user'
                                 else self.role in rule['service_roles'])
        if scope not in self.scopes or not allowed_role:
            raise HTTPException(403, 'forbidden')


def rate_limit(settings, actor):
    if settings.requests_per_minute <= 0:
        return actor
    key = f'xmux:rate:{actor.tenant}:{actor.principal}'
    try:
        client = Redis.from_url(settings.redis_url, socket_timeout=2, socket_connect_timeout=2)
        try:
            count = client.eval("local n=redis.call('INCR',KEYS[1]); "
                                "if n==1 then redis.call('EXPIRE',KEYS[1],60) end; return n", 1, key)
        finally:
            client.close()
    except RedisError:
        raise HTTPException(503, 'unavailable') from None
    if count > settings.requests_per_minute:
        raise HTTPException(429, 'rate_limited')
    return actor


def authenticate(request: Request):
    settings = request.app.state.settings
    header = request.headers.get('authorization', '')
    if not header.startswith('Bearer ') or len(header) > 16384:
        raise HTTPException(401, 'auth_failed')
    token = header[7:]
    try:
        registry = json.loads(Path(settings.auth_registry_file).read_text()) if settings.auth_registry_file else {}
    except (OSError, ValueError):
        raise HTTPException(503, 'unavailable') from None
    digest = hashlib.sha256(token.encode()).hexdigest()
    for entry in registry.get('tokens', []):
        if hmac.compare_digest(digest, entry['sha256']):
            if settings.environment != 'development' and entry.get('role') == 'user':
                continue
            return rate_limit(settings, Actor(UUID(entry['tenant_id']), UUID(entry['principal_id']),
                         frozenset(entry['scopes']), entry['role']))
    if settings.jwt_issuer and settings.jwt_public_key_file:
        try:
            claims = jwt.decode(token, Path(settings.jwt_public_key_file).read_text(), algorithms=['RS256'],
                                issuer=settings.jwt_issuer, audience=settings.jwt_audience,
                                options={'require': ['exp', 'iat', 'iss', 'aud', 'sub']})
            # Tokens identify a subject; privileges are bounded by server-side account configuration.
            entry = registry.get('subjects', {}).get(claims['sub'])
            if entry:
                granted = set(claims.get('scope', '').split()) & set(entry['scopes'])
                return rate_limit(settings, Actor(UUID(entry['tenant_id']), UUID(entry['principal_id']), frozenset(granted), 'user'))
        except jwt.InvalidTokenError:
            pass
    raise HTTPException(401, 'auth_failed')
