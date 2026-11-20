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
