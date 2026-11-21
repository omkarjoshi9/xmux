# Control-plane development

The control plane uses Python 3.12 to match the multiplexer implementation. Shared JSON schemas remain language-neutral. Install only the owned apps' tooling; other developers retain their own service modules.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r infrastructure/control-plane/requirements.lock
cp .env.example .env
# Set POSTGRES_PASSWORD and the matching DATABASE_URL password in .env.
make deps
make migrate
make lint test build
make up
```

If the host lacks ensurepip, install its Python venv package or bootstrap the already-created environment with a working pip: `python3 -m pip --python .venv/bin/python install pip -r infrastructure/control-plane/requirements.lock`.

PostgreSQL/Redis and development service ports bind only to loopback. Production ingress should expose only the HTTPS reverse proxy; the Caddyfile is a template requiring owned DNS names and server access. It does not deploy staging. Production service credentials must be supplied through environment or `*_FILE` secret mounts, not committed files. Local `.env` is ignored. Runtime API/MCP authentication is implemented in subsequent phases; P1 exposes only liveness/readiness.

`/health/live` reports process liveness. `/health/ready` checks PostgreSQL and Redis and returns 503 without leaking dependency URLs when either fails. Structured application logs allowlist diagnostic fields and exclude raw message/exception content. Uvicorn access logs are disabled to avoid private query strings.

The API, MCP process, worker and migration runner have separate Compose entrypoints. The P1 worker is an idle process scaffold, not a job executor. Migrations are applied under a PostgreSQL advisory lock and recorded with a checksum; changing an applied migration is rejected. Add a new migration instead.

`make down` stops containers without deleting persistent volumes. `make contracts` runs the shared contract suite. `make test` runs owned Python tests. `make build` verifies source compilation; container builds additionally verify pinned dependency installation.

## Ledger and transactions

Migration 0002 creates owner-scoped records for identities, events, tasks, jobs, approvals, delivery, workflows, routines, outbox and audit history. Public access must use both tenant and principal filters. Child records are accessed through their owning task; composite foreign keys reject cross-owner event/workflow association. Event IDs are deduplicated globally by platform and the adapter's fully qualified external identifier. Intake creates one task per event atomically.

Task creation serializes its owner/operation/key with a transaction advisory lock. The task, fingerprint reservation, audit record and outbox notification commit together. Fingerprints use the shared RFC8785 projection. Network calls must occur outside transactions. Outbox claims use `SKIP LOCKED` and fencing tokens; lease expiration permits recovery, but never proves a remote action did not execute. Dispatch recovery is implemented in P4.

Tests create a uniquely named `xmux_test_*` database and remove only that database. The development/CI database role needs CREATEDB. Production roles should not have that privilege. Migrations repeat safely, reject changed checksums, and are serialized with a transaction lock. Forward-only migrations are the default; restore testing and production role separation follow in P9.

Retention policy: keep idempotency reservations while the action can be replayed, and keep remote IDs until reconciliation and the configured audit window end. Expire source text/results separately from coordination references. Do not delete an uncertain dispatch or active routine as ordinary content cleanup. Physical owner deletion requires an explicitly authorized maintenance procedure because foreign keys and append-only audit triggers intentionally prevent casual cascading loss. Production retention configuration and cleanup are part of hardening.

## Authenticated intake

Set `AUTH_REGISTRY_FILE` to a mounted JSON registry containing `tokens` (SHA-256 bearer token hashes, tenant_id, principal_id, role, scopes) and/or `subjects` (OAuth subject -> server-owned tenant/principal/scopes). Development user tokens work only with `XMUX_ENV=development`; production users require RS256 JWTs with configured issuer, audience, expiry and issued-at claims. Set `JWT_ISSUER`, `JWT_AUDIENCE` and `JWT_PUBLIC_KEY_FILE` for the pinned verification key. Public key rotation must update the mounted key. Services use separately scoped hashed credentials. Provision principals/verified channel mappings through trusted administration; request bodies cannot provision identities.

Task create/read/list/result/cancel endpoints share PostgreSQL ownership checks. Gateway `/v1/events` requires a matching verified identity reference and currently accepts explicit `/research`, `/summarize`, or `/delegate` commands. Ambiguous natural language is rejected. It records supplied URLs without fetching arbitrary content. Running tasks cannot yet be cancelled through this intake phase; unsupported remote cancellation is explicit.

`tests/integration/mocks/services.py:create_mock(token, scenario)` supplies authenticated mock dispatch/delivery endpoints for local teammate integration. Scenarios: complete, failure, offline, timeout_before, timeout_after. `/mock/*` routes are test harness facilities, not agreed production multiplexer routes. The injected mock keeps in-memory remote state; PostgreSQL durability tests use real databases separately. The real multiplexer must agree status/reconciliation endpoints before deployment.

## Dispatch recovery (P4 work in progress)

The worker now claims PostgreSQL outbox entries with fenced leases, records dispatch intent before network I/O, validates remote receipts and results, and recovers uncertain dispatch through correlation lookup. It never resubmits an uncertain action. Configure `MULTIPLEXER_RECONCILE_PATH` and `MULTIPLEXER_RESULT_PATH` only for verified adapter endpoints. These routes are not assumed to exist on the teammate's service.

Authenticated result callbacks use `/internal/callbacks`, require the multiplexer role and `tasks:observe`, and verify tenant plus the persisted remote receipt. Callback IDs/sequences prevent duplicate or conflicting terminal updates. Keep this route on private ingress.

Pending P4 work: registry-based selection/capability checks, supported remote cancellation, safe retry execution after authoritative absence, and an operator dead-letter replay command. Current authoritative absence transitions to RETRY_PENDING but requires review; it does not automatically resubmit. Missing reconciliation stays UNKNOWN and exhausts into a dead-letter record after bounded attempts. Non-research/summarize tasks wait for approval; approval issuance/consumption follows P6.
