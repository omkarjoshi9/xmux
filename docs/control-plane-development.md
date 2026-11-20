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
