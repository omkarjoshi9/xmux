"""OAuth-protected, stateless MCP adapter to the owner-authorized HTTP API."""
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

import httpx
import jwt
from jsonschema import ValidationError
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from xmux_control.contracts import validate

LOG = logging.getLogger(__name__)
SCOPES = {'capture_content': 'tasks:create', 'create_task': 'tasks:create',
          'delegate_task': 'tasks:create', 'get_task_status': 'tasks:read',
          'get_result': 'tasks:read', 'list_agents': 'agents:read',
          'approve_action': 'approvals:decide', 'list_routines': 'routines:read',
          'create_routine': 'routines:create', 'search_references': 'references:read'}


@dataclass(frozen=True)
class Config:
    issuer: str
    resource: str
    public_key_file: str
    accounts_file: str
    api_url: str
    routes: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls):
        return cls(*(os.environ['XMUX_MCP_' + key] for key in
                     ('ISSUER', 'RESOURCE', 'PUBLIC_KEY_FILE', 'ACCOUNTS_FILE', 'API_URL')),
                   routes=json.loads(os.environ.get('XMUX_MCP_ROUTES', '{}')))


class Verifier:
    def __init__(self, config):
        self.config = config

    async def verify_token(self, token):
        try:
            claims = jwt.decode(token, Path(self.config.public_key_file).read_text(),
                                algorithms=['RS256'], issuer=self.config.issuer,
                                audience=self.config.resource,
                                options={'require': ['exp', 'iat', 'iss', 'aud', 'sub']})
            accounts = json.loads(Path(self.config.accounts_file).read_text())
            account = accounts[claims['sub']]
            UUID(account['tenant_id'])
            UUID(account['principal_id'])
            scopes = sorted(set(claims.get('scope', '').split()) & set(account['scopes']))
            return AccessToken(token=token, client_id=claims['sub'], subject=claims['sub'],
                               scopes=scopes, expires_at=int(claims['exp']), resource=self.config.resource)
        except (jwt.InvalidTokenError, KeyError, ValueError, TypeError, OSError):
            return None


def create_app(config=None, transport=None):
    config = config or Config.from_env()
    for url in (config.issuer, config.resource, config.api_url):
        parsed = urlsplit(url)
        if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in {'localhost', '127.0.0.1'}):
            raise ValueError('HTTPS required except on loopback')
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Invalid service URL')
    def check_configuration():
        try:
            from cryptography.hazmat.primitives.serialization import load_pem_public_key
            load_pem_public_key(Path(config.public_key_file).read_bytes())
            accounts = json.loads(Path(config.accounts_file).read_text())
            if not isinstance(accounts, dict) or not accounts:
                raise ValueError()
            for account in accounts.values():
                UUID(account['tenant_id'])
                UUID(account['principal_id'])
                if not isinstance(account['scopes'], list) or not account['api_access_token']:
                    raise ValueError()
        except (OSError, ValueError, KeyError, TypeError):
            raise ValueError('invalid_mcp_configuration') from None

    check_configuration()
    host = urlsplit(config.resource).netloc
    server = FastMCP('XMUX', token_verifier=Verifier(config), stateless_http=True,
                     json_response=True, max_request_body_size=65536,
                     auth=AuthSettings(issuer_url=config.issuer, resource_server_url=config.resource,
                                       required_scopes=[], validate_token_resource=True),
                     transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True,
                         allowed_hosts=[host], allowed_origins=[f'{urlsplit(config.resource).scheme}://{host}']))

    async def call(tool, method, path, payload=None):
        if tool in config.routes:
            # Overrides are operator-controlled relative paths, never client-controlled URLs.
            original_id = path.split('/')[-2] if path.endswith(('/result', '/decision')) else path.split('/')[-1]
            path = config.routes[tool].replace('{id}', original_id)
        if not path.startswith('/v1/') or '..' in path or '?' in path or '#' in path:
            raise ToolError('invalid_route_configuration')
        token = get_access_token()
        if token is None or SCOPES[tool] not in token.scopes:
            raise ToolError('insufficient_scope: ' + SCOPES[tool])
        try:
            check_configuration()
            accounts = json.loads(Path(config.accounts_file).read_text())
            account = accounts.get(token.subject)
        except (OSError, ValueError, TypeError):
            raise ToolError('account_configuration_unavailable') from None
        if not account or SCOPES[tool] not in account['scopes']:
            raise ToolError('forbidden')
        upstream_token = account.get('api_access_token')
        if not upstream_token or upstream_token == token.token:
            raise ToolError('upstream_credential_unavailable')
        outcome = 'unavailable'
        try:
            async with httpx.AsyncClient(base_url=config.api_url, transport=transport, timeout=15,
                                         follow_redirects=False) as client:
                identity = await client.get('/v1/identity', headers={'Authorization': 'Bearer ' + upstream_token})
                if identity.status_code != 200:
                    raise ToolError('upstream_identity_unavailable')
                who = identity.json()
                if any(who.get(k) != account.get(k) for k in ('tenant_id', 'principal_id')):
                    raise ToolError('upstream_identity_mismatch')
                if SCOPES[tool] not in who.get('scopes', []):
                    raise ToolError('upstream_scope_denied')
                async with client.stream(method, path, json=payload if method == 'POST' else None,
                                         params=payload if method == 'GET' else None,
                                         headers={'Authorization': 'Bearer ' + upstream_token}) as response:
                    outcome = str(response.status_code)
                    if response.status_code in (404, 405, 501):
                        raise ToolError('not_found_or_unsupported_capability')
                    if response.status_code >= 300:
                        raise ToolError('upstream_rejected: ' + outcome)
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > 65536:
                            raise ToolError('output_too_large')
                    return json.loads(data)
        except (httpx.HTTPError, ValueError):
            raise ToolError('upstream_unavailable') from None
        finally:
            LOG.info('mcp_tool tool=%s principal=%s outcome=%s', tool, account['principal_id'], outcome)

    async def task(tool, request):
        try:
            validate('TaskRequest', request)
        except ValidationError:
            raise ToolError('validation_failed') from None
        return await call(tool, 'POST', '/v1/tasks', request)

    @server.tool()
    async def create_task(request: dict) -> dict:
        """Create durable work. Supply a shared TaskRequest and stable idempotency_key."""
        return await task('create_task', request)

    @server.tool()
    async def delegate_task(request: dict) -> dict:
        """Create work with requested_agent; same payload/key as create_task means same task."""
        if not request.get('requested_agent'):
            raise ToolError('requested_agent_required')
        return await task('delegate_task', request)

    @server.tool()
    async def capture_content(request: dict) -> dict:
        """Capture explicitly supplied content as a durable task; never fetch arbitrary URLs.

        Supply TaskRequest with objective containing content and optional context_refs.
        """
        return await task('capture_content', request)

    @server.tool()
    async def get_task_status(task_id: UUID) -> dict:
        """Read caller-owned ledger status; no unsupported remote refresh."""
        return await call('get_task_status', 'GET', f'/v1/tasks/{task_id}')

    @server.tool()
    async def get_result(task_id: UUID) -> dict:
        """Read caller-owned result references."""
        return await call('get_result', 'GET', f'/v1/tasks/{task_id}/result')

    @server.tool()
    async def list_agents() -> dict:
        """Read the authorized agent registry."""
        return await call('list_agents', 'GET', '/v1/agents')

    @server.tool()
    async def approve_action(approval_id: UUID, decision: dict) -> dict:
        """Submit exact approval decision; API enforces ownership, fingerprint and expiry."""
        return await call('approve_action', 'POST', f'/v1/approvals/{approval_id}', decision)

    @server.tool()
    async def list_routines() -> dict:
        """Read caller-owned remote routine references."""
        return await call('list_routines', 'GET', '/v1/routines')

    @server.tool()
    async def create_routine(request: dict) -> dict:
        """Delegate routine creation through the API; never create a local scheduler."""
        return await call('create_routine', 'POST', '/v1/routines', request)

    @server.tool()
    async def search_references(query: str, limit: int = 20) -> dict:
        """Search authorized stored references. Remote memory requires upstream support."""
        if not 1 <= len(query) <= 2000 or not 1 <= limit <= 100:
            raise ToolError('validation_failed')
        return await call('search_references', 'GET', '/v1/references/search', {'query': query, 'limit': limit})

    from starlette.responses import JSONResponse

    @server.custom_route('/healthz', methods=['GET'])
    async def health(request):
        try:
            check_configuration()
        except ValueError:
            return JSONResponse({'status': 'unavailable'}, status_code=503)
        return JSONResponse({'status': 'ok'})

    return server.streamable_http_app()
