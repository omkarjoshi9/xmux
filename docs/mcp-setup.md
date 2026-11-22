# Remote MCP setup

XMUX uses the installed official Python MCP SDK, stateless Streamable HTTP at
`/mcp`, JSON responses, bounded request/output bodies and DNS rebinding protection.
It delegates lifecycle and ownership decisions to the existing orchestrator API.
It does not implement a second task ledger or scheduler.

## Configuration

Run from the repository root with the existing virtual environment:

```sh
export PYTHONPATH=apps/mcp-server/src:apps/orchestrator/src
.venv/bin/uvicorn xmux_mcp.app:create_app --factory --host 127.0.0.1 --port 8001
```

Required variables (no defaults or development authentication bypass):

- `XMUX_MCP_ISSUER`: exact HTTPS OAuth issuer.
- `XMUX_MCP_RESOURCE`: canonical public endpoint, e.g. `https://mcp.example.com/mcp`.
- `XMUX_MCP_PUBLIC_KEY_FILE`: issuer RS256 public PEM key, provisioned out of band.
- `XMUX_MCP_ACCOUNTS_FILE`: private JSON subject mapping, described below.
- `XMUX_MCP_API_URL`: orchestrator origin. HTTPS required except loopback.
- Optional `XMUX_MCP_ROUTES`: JSON tool-to-relative-path overrides. Use `{id}` for task/approval IDs. Only `/v1/` paths are allowed.

Account mapping format:

```json
{
  "issuer-subject": {
    "tenant_id": "00000000-0000-0000-0000-000000000001",
    "principal_id": "00000000-0000-0000-0000-000000000002",
    "scopes": ["tasks:create", "tasks:read", "agents:read"],
    "api_access_token": "PROVISION_SEPARATE_USER_API_ACCESS_TOKEN"
  }
}
```

Restrict this file to the service user. Every subject's API credential MUST belong
to the same tenant/principal in the orchestrator's authenticated subject registry.
The API credential is separately issued for the API audience. The MCP bearer token
is never forwarded. Production API authentication requires a user JWT; service
credentials cannot create user-owned tasks. Scope grants intersect issuer claims
with this server-side allowlist. The API independently validates its credential,
scopes and record ownership. Rotate API credentials before expiry and provision
issuer key rotation externally; automatic token exchange/JWKS refresh is not implemented.
Never configure a shared privileged API credential for multiple users.

## Parent integration contract

| Tool | Scope | Default upstream request |
| --- | --- | --- |
| capture_content | tasks:create | POST /v1/tasks |
| create_task | tasks:create | POST /v1/tasks |
| delegate_task | tasks:create | POST /v1/tasks |
| get_task_status | tasks:read | GET /v1/tasks/{id} |
| get_result | tasks:read | GET /v1/tasks/{id}/result |
| list_agents | agents:read | GET /v1/agents |
| approve_action | approvals:decide | POST /v1/approvals/{id}/decision |
| list_routines | routines:read | GET /v1/routines |
| create_routine | routines:create | POST /v1/routines |
| search_references | references:read | GET /v1/references/search?query=...&limit=... |

Task tools accept `request`, the existing TaskRequest contract, validated locally.
Delegate additionally requires `requested_agent`. All three preserve the same
payload and idempotency key, so aliases share API deduplication. Capture stores
explicit content in `objective` and references in `context_refs`; use intent
`save_reference`. It does not crawl URLs or pretend to implement channel capture.
Status reads the ledger; no unsupported remote refresh is advertised.

Approval accepts `approval_id` and a `decision` object, passed unchanged. Routine
creation accepts `request`, passed unchanged. These routers must enforce their
shared schemas, exact action fingerprints, expiry, permissions and owner mapping.
Agent listing must be an authorized registry view. Reference search must filter
by API identity and enforce destination capability before remote lookup.
Parent must supply these routers or override their paths; 404/405/501 return
`not_found_or_unsupported_capability`, never simulated success. Other API errors
return sanitized status codes. No API/settings/requirements files were modified.

## OAuth and client setup

The SDK serves RFC 9728 protected resource metadata and includes its location in
401 challenges. Configure the issuer's authorization-server discovery, OAuth 2.1
Authorization Code + PKCE flow, exact client redirect URIs, resource indicator
and requested tool scopes. Issued access JWTs need RS256 signature, `iss`, `aud`
matching the MCP resource, `sub`, `iat`, and `exp`. Link that subject server-side.
Point a compatible remote MCP client at the public HTTPS `/mcp` endpoint; verify
initialize, tools/list, then list_agents/create_task/get_task_status/get_result.
Use a proxy preserving Host and Authorization and enforcing suitable request limits.
Tool-specific scope errors are MCP tool errors; baseline authentication is HTTP 401.

Requirements checked against official [authorization specification](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/authorization/index.mdx),
[discovery requirements](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/authorization/authorization-server-discovery.mdx),
and [Streamable HTTP specification](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/transports/streamable-http.mdx).
Protocol negotiation remains governed by the installed SDK; tests exercise
2025-11-25, not full conformance to every newer protocol revision.

## Evidence and remaining blockers

```sh
PYTHONPATH=apps/mcp-server/src:apps/orchestrator/src .venv/bin/pytest apps/mcp-server/tests -q
```

Tests use actual SDK HTTP JSON-RPC routing and signed RSA JWTs. They exercise
initialization, ten tool successes, metadata, missing auth, wrong issuer/audience,
expired tokens, unlinked subjects, scope denial, alias payload identity, separate
API credentials and propagation of an upstream cross-owner denial without leakage.
The upstream is mocked: this is not evidence of live IdP, database ownership,
agent registry, remote scheduling or target-client compatibility. Live acceptance
requires a deployed issuer, subject/account linking, separate API user credentials,
public TLS endpoint, completed upstream routers and a configured client. P7 live
acceptance remains blocked until those are provided.
