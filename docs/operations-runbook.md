# Control-plane operations

## Deployment

Keep database, Redis, multiplexer, delivery, callbacks and metrics on a private network. Expose only the authenticated API/MCP via HTTPS. Development Compose binds ports to loopback; it is not a production deployment. Supply secrets through mounted files or a secret manager. Never commit `.env` or print resolved Compose configuration.

Use a migration role for schema changes and separate runtime roles without SUPERUSER, CREATEDB or schema ownership. Apply migrations before API/worker rollout. Do not modify migration files after deployment. Take a backup before upgrading; roll back application images only when compatible with the migrated schema.

Configure issuer, audience, pinned verification keys and server-owned subject mappings before admitting production users. Development user tokens are rejected in production. Rotate verification keys with the identity provider; the current pinned-key verifier requires coordinated key updates. Internal service credentials must be distinct and scope-limited.

## Health and incident response

Liveness reports process availability. Readiness requires PostgreSQL and Redis. Check these first when workers stop progressing. Stop dispatch workers during an upstream outage if necessary; retain PostgreSQL records and volumes. Restarting a worker must reconcile prior dispatch intent before retrying it.

For UNKNOWN tasks, inspect correlation/remote IDs through authorized administration. Never change UNKNOWN to QUEUED based on a timeout, missing search result or operator intuition. Require authoritative non-acceptance with closure of in-flight acceptance, or find the original remote task. Keep dead-letter records until investigated. A successful task with failed delivery remains successful; recover its notification separately.

Never log request bodies, bearer tokens, private source text or database URLs. Metrics must use bounded labels such as status codes, not task IDs, principals, URLs or objectives. Keep metrics behind service authentication and private ingress.

## Backup policy and restore drill

Target daily encrypted PostgreSQL backups, seven daily and four weekly copies, stored outside the host. Set an explicit retention policy with the owner before deployment; these are defaults, not a claim of an installed schedule. Monitor backup age and failures. Recovery point target: 24 hours. Recovery time target: 4 hours, validated by a timed drill on the actual host.

`infrastructure/operations/backup.sh destination.dump` writes a private custom-format dump without placing credentials in command arguments. Supply `DATABASE_URL` from the secret store. Encrypt before moving the dump off-host. PostgreSQL client tools must match the server major version.

Restore into a fresh isolated database or instance, never over the live database. Validate migrations, task counts, owner isolation, remote correlation records and pending outbox entries. Stop restored workers until the operator confirms the environment: a restored ledger may contain already-executed remote actions. Run reconciliation, not blind dispatch. Record backup timestamp, restore duration and sanitized test results.

Content retention must exclude active/uncertain tasks and remote schedule ownership references. Redact expired source payloads separately from coordination metadata. Audit deletion requires an explicit policy and maintenance role; append-only triggers protect routine writes.

## External release gates

Production DNS/TLS, secrets provisioning, live identity-provider/client configuration, real gateway and agent connectivity, and a timed off-host restore drill need deployment access. Local tests and mocks do not establish these gates.

## Compose secret mounts and MCP profile

Provision `.runtime/auth-registry.json`, `.runtime/api-public.pem`, `.runtime/mcp-public.pem` and `.runtime/mcp-accounts.json` as applicable. This directory is ignored by Git and Docker builds and mounted read-only at `/run/xmux`. Restrict ownership/read permissions to the container UID 10001. Do not use empty placeholder credentials for production.

Set service tokens and the cursor/metrics secrets through the deployment secret environment (or override Compose with `*_FILE` mounts). Container filesystems are read-only with temporary `/tmp`; all Linux capabilities are dropped. API ingress rejects `/internal/*` and health paths; internal callbacks/metrics remain reachable only on the private service network.

MCP is an explicit Compose `mcp` profile. Provision its issuer/resource/API URL and account/key files, then run `docker compose --env-file .env -f infrastructure/compose/compose.yml --profile mcp up -d --build`. The MCP API URL must satisfy its HTTPS/loopback policy; use the authenticated HTTPS API origin in deployment. No default credentials or anonymous MCP fallback are supplied.

Authenticated requests use a Redis-backed per-tenant/principal fixed one-minute limit when `REQUESTS_PER_MINUTE` is positive. Compose defaults to 120; Redis failure rejects requests rather than bypassing the configured limit. Apply unauthenticated IP/channel limits at the public gateway as well. Metrics expose bounded task/outbox state counts, oldest pending age and HTTP latency/status counts. Dedicated remote latency, approval wait and routine reachability dashboards remain deployment follow-ups.
