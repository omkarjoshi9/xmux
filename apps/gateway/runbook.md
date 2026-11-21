# XMUX channel gateway

## Run locally

From the repository root, use Python 3.12, install dependencies, and run the test suite.

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r apps/gateway/requirements.txt
python -m unittest discover -s tests -v
```

Copy `apps/gateway/.env.example` to `.env`, fill it with staging settings, and export the values through your service environment or secret manager. Launch from the repository root.

```sh
uvicorn apps.gateway.src.main:app --port 8080 --no-access-log
docker build -f apps/gateway/Dockerfile -t xmux-gateway .
```

Use one gateway process with a persistent writable SQLite volume. The worker runs in the API process. Restart recovery assumes the previous process has exited. Schedule storage backups if you need to preserve replay and delivery state across disk failures. Keep the database and its WAL private. The worker removes private message text from completed queue records and expires Discord interaction tokens. Apply backup retention and run SQLite maintenance according to your data policy.

Publish only the webhook routes through the HTTPS reverse proxy. Limit request rates at the proxy and block egress to private and infrastructure networks as a second layer for public URL fetching. Keep the delivery API, readiness, and API docs on the private network. The gateway applies a 20-event-per-minute fixed-window limit per platform identity. Telegram and the Discord message listener drop events over that limit, and slash commands receive a private rate-limit response.

## Platform configuration

| Platform | Receive | Reply and access requirements |
|---|---|---|
| Telegram | BotFather bot. Configure `setWebhook` at `/webhooks/telegram` with `secret_token` and `allowed_updates: ["message"]`. Verify DMs and commands addressed to the bot in a test group. Group visibility depends on Telegram privacy mode and permissions. | Allow the bot to send messages and reply in the target chat or topic. Supports text, URLs, forwarded/reply references, and attachment metadata. Edits are ignored. |
| Discord | Install the application with `bot` and `applications.commands`. Point the interactions endpoint at `/webhooks/discord`. Register `/capture` with an optional string `text` and `/link` with a required string `code`. Enable bot-DM command context for linking. The authenticated Gateway connection receives DMs and bot mentions. Start with Message Content intent disabled and verify ordinary platform exemptions. | For message replies, grant View Channel, Send Messages, Read Message History, and Send Messages in Threads where used. Slash responses are ephemeral, and interaction tokens expire. Supports text, URLs, and attachment metadata. Edits are ignored. |
| Slack | Stretch, after Telegram and Discord acceptance. | Requires app installation and workspace scopes. |
| Meta | Feasibility only. Check Instagram professional-account eligibility and WhatsApp Business onboarding, approved messaging scopes, webhook availability, and app review before committing an implementation. | Verify reply windows, approval requirements, and which media or mentions the account can access in the current official documentation. |
| LinkedIn, X, Reddit | Follow-ups. Confirm supported Page or bot events, API tier or approval, and permitted use for each. | Confirm outbound replies and content access separately. |

## Dev 3 integration

All calls below require a service bearer token. Dev 3 should enforce task permissions using the verified identity and treat captured text and extracted page content as untrusted data.

- Gateway → `POST /v1/channel-identities/resolve` with `tenant_id`, `platform`, and `actor_external_id`. Return `{"principal_id":"<uuid>","permissions":["capture"]}` or 404 for an unlinked identity. The gateway rejects unlinked task submissions.
- Gateway → `POST /v1/channel-identities/link` with the same identity fields plus `code` and `event_id`. Dev 3 issues and atomically consumes the one-time code, and returns the same successful result for a replay of the same event. Use 400, 404, 409, or 422 for rejected codes. Link redemption is available only in private chats.
- Gateway → `POST /v1/events` with `EventEnvelope.v1.json`. Dev 3 deduplicates by platform and platform event ID and returns the same acceptance and task ID on safe retry. The gateway uses the same stable event UUID for retries.
- Dev 3 → `POST /v1/deliveries` with the task ID, origin channel and unchanged reply target, plus concise response text and artifact URLs. An identical normalized request returns the same delivery ID. Poll `GET /v1/deliveries/{delivery_id}` for its state.

```http
POST /v1/deliveries
Authorization: Bearer <GATEWAY_SERVICE_TOKEN>
Content-Type: application/json

{"task_id":"a0b1c2d3-e4f5-4a67-89ab-123456789012","channel":"telegram","reply_target":{"channel_id":"123","message_id":"42"},"text":"The summary is ready.","artifacts":["https://example.org/result"]}
```

Use `/readyz` to check the database, worker, and the Discord listener when enabled. Delivery `failed` indicates a terminal error. Delivery `unknown` means a timeout, provider 5xx, or process interruption left a send uncertain. Check the originating conversation or reconcile with the provider before making a new delivery request. The gateway retries explicit 429s and resumes after the last acknowledged chunk.

## Reproducible demos

These tests exercise the webhooks, durable worker, mock orchestrator, and platform responses.

```sh
python -m unittest tests.gateway.test_gateway.GatewayTests.test_telegram_round_trip -v
python -m unittest tests.gateway.test_gateway.GatewayTests.test_discord_round_trip -v
```

For staging acceptance, link one identity per platform, submit a capture on each, confirm that Dev 3 created one task per event, and verify that each result arrives in the originating chat with its expected task ID. Test Discord DMs and group mentions separately because their intake uses the authenticated Gateway connection.
