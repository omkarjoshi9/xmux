# MCP integration

Parent must install `xmux_control.catalog_router.install(app, ledger,
registry_url=..., registry_token=...)` in the orchestrator factory. The registry
URL is the full authorized catalog endpoint, not a Dev2 routing endpoint.
The remote registry must enforce the delegated `X-Xmux-Tenant` and
`X-Xmux-Principal` identity with the configured service credential. Use HTTPS.
Absent registry configuration returns 503. No agent/routine live service is
available yet; adapters remain configurable and the live acceptance gate remains open.

Mount separate API and MCP RSA public keys and account registries read-only.
Set XMUX_MCP_ISSUER, XMUX_MCP_RESOURCE, XMUX_MCP_PUBLIC_KEY_FILE,
XMUX_MCP_ACCOUNTS_FILE, XMUX_MCP_API_URL. URLs require HTTPS except loopback.
Production Compose must provide internal TLS for the API URL rather than relax
this check. Accounts map MCP subject to tenant_id, principal_id, scopes and
api_access_token. API credentials must use the API audience/signing key and map
to exactly the same tenant/principal. Each tool checks `/v1/identity` before
forwarding, including the upstream scope. Never forward the incoming MCP token.

Parent Compose command:
`uvicorn xmux_mcp.app:create_app --factory --host 0.0.0.0 --port 8000 --no-access-log`.
Missing configuration prevents startup. Probe `/healthz` for configuration
health (not upstream readiness); use the resource hostname in the Host header.
The shared Docker image already includes MCP source/dependencies. Do not use
an unauthenticated placeholder bootstrap. Keep secrets out of Compose literals.

Approval default is `/v1/approvals/{id}`, compatible with phase6_router.
XMUX_MCP_ROUTES can override tool paths with relative `/v1/` paths and `{id}`.
Parent must install phase6_router separately. No shared files changed here.

Run from repository root, with DATABASE_URL pointing to a PostgreSQL role with
CREATE DATABASE permission (fixture creates, migrates, and drops an isolated DB):

```sh
PYTHONPATH=apps/orchestrator/src:apps/mcp-server/src .venv/bin/pytest apps/mcp-server/tests -q
```

Parent CI must set DATABASE_URL and run this directory. Missing DB is a hard
failure, not a skip. Integration uses real MCP HTTP, ASGI orchestrator, production
JWT authentication with separate signing keys, and PostgreSQL. It covers create,
owner read, cross-owner denial, MCP scope denial and misbound upstream tokens.
