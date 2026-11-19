# P0.1 — Shared-contract workshop

Status: proposed; no team agreement recorded.
Prepared: 2026-11-19.
Scope: first task of Phase 0. Prepare the 90-minute workshop, reconcile source discrepancies, and propose example payloads. Executable schemas, OpenAPI generation, fixtures and service implementations are subsequent tasks.

The Developer 3 work order is the detailed implementation input. The broader architecture defines the product and federation boundaries. Where they differ, the choices below are proposals for review, not established cross-team contracts.

## Workshop outcome

All three module owners should leave with one wire vocabulary and a list of explicit unresolved dependencies. The control plane owns coordination; the gateway owns platform verification/extraction/delivery; the multiplexer owns routing and agent execution interfaces.

| Minutes | Discussion                             | Required outcome                                                |
| ------- | -------------------------------------- | --------------------------------------------------------------- |
| 0–10    | Ownership, federation and MVP boundary | Confirm who produces/consumes each contract                     |
| 10–25   | Event shape, IDs, time and identity    | Confirm EventEnvelope v1 and trusted actor mapping              |
| 25–40   | Task API and lifecycle                 | Confirm task fields, states and approval/cancellation semantics |
| 40–55   | Dispatch and uncertain outcomes        | Confirm endpoint, correlation lookup and safe retry guarantees  |
| 55–65   | Delivery handoff                       | Confirm receipt vs final delivery and deduplication             |
| 65–75   | Workflow, routine and MCP boundaries   | Confirm capabilities and tool inventory                         |
| 75–85   | Versioning, errors and examples        | Confirm conventions and fixture scenarios                       |
| 85–90   | Decisions and handoffs                 | Record accepted choices, objections, owners and deadlines       |

## Proposed resolutions

| Topic                 | Source discrepancy                                  | Proposed v1 decision                                                                                                 | Review owners               |
| --------------------- | --------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- | --------------------------- |
| Agent dispatch        | `/v1/dispatch` vs `/internal/agents/dispatch`       | Use `POST /v1/dispatch` on the private multiplexer service; private networking and authentication determine exposure | Control plane + multiplexer |
| Channel delivery      | `/v1/deliveries` vs `/internal/delivery`            | Use `POST /v1/deliveries` on the private gateway service                                                             | Control plane + gateway     |
| External event key    | `platform_event_id` vs `external_event_id`          | Wire field `platform_event_id`; database naming may differ                                                           | All                         |
| Source user           | `actor_external_id` vs `platform_user_id`           | Use `actor_external_id`; gateway maps the verified platform actor                                                    | All                         |
| Principal             | Payload `principal_id` vs actor/tenant/auth context | Resolve principal from authenticated trusted mappings; caller-supplied identity claims alone grant no authority      | All                         |
| Tenant                | Work order explicitly includes `tenant_id`          | Include it, but validate against authenticated caller and actor mapping; never trust an arbitrary tenant value       | All                         |
| Event time            | `occurred_at` vs `received_at`                      | Source supplies UTC occurrence time; control plane stamps UTC receipt time                                           | All                         |
| Links                 | `urls` vs `source_urls`                             | Use `urls` for source URLs; keep extracted content references separate                                               | All                         |
| Success state         | `SUCCEEDED` vs `completed`                          | Use uppercase `SUCCEEDED` to match the focused work order                                                            | All                         |
| Approval/retry states | Missing from shortened work-order graph             | Include `AWAITING_APPROVAL`, `RETRY_PENDING` and `REJECTED`; preserve `UNKNOWN` for remote execution uncertainty     | All                         |
| IDs                   | UUID requirement vs prefixed examples               | XMUX event/task IDs are UUIDs; platform/remote IDs remain opaque strings                                             | All                         |
| MCP inventory         | Different tool lists                                | Cover the combined inventory below; optional remote abilities stay capability-gated                                  | Control plane + multiplexer |
| Versions              | Versioning required but format unspecified          | `schema_version: "1"` in shared resource envelopes; breaking payload changes require a new agreed version            | All                         |

No compatibility aliases or translation layer should be implemented unless an actual integration needs them and the team agrees on their behavior.

## Proposed payloads for discussion

These examples illustrate shape and trust boundaries. They are not executable JSON Schemas or committed test fixtures yet. Authentication credentials belong in transport headers, never captured text or payload fixtures.

### Gateway to control plane: `POST /v1/events`

```json
{
  "schema_version": "1",
  "event_id": "39b5ee0b-6ecf-4ef8-8953-a34a8272ec90",
  "platform": "telegram",
  "platform_event_id": "chat_42:msg_7",
  "event_type": "direct_message",
  "occurred_at": "2026-11-19T12:00:00Z",
  "actor_external_id": "telegram_user_123",
  "tenant_id": "44331ef6-b679-4ecb-bd83-fef7d89d25cd",
  "text": "Ask Hermes to investigate this claim",
  "urls": ["https://example.org/post"],
  "attachments": [],
  "reply_target": {
    "channel_id": "chat_42",
    "message_id": "msg_7"
  },
  "auth_context": {
    "identity_mapping_ref": "mapping_123"
  }
}
```

The gateway must derive the actor and mapping reference from verified platform context. The control plane authenticates that gateway, resolves the reference in a trusted server-side mapping and validates tenant membership and permissions. A reference appearing in arbitrary message text is not proof of identity. The mapping lookup/provisioning interface must be agreed before implementation.

Deduplicate by `(platform, platform_event_id)`. The gateway must namespace platform event IDs when a message ID is only unique within a chat/account. Replays with a newly generated XMUX event UUID must still identify the same source event. A replay from an unauthorized actor must not reveal another actor's task.

### Authenticated task creation: `POST /v1/tasks`

```json
{
  "schema_version": "1",
  "idempotency_key": "telegram:chat_42:msg_7:research",
  "source_event_id": "39b5ee0b-6ecf-4ef8-8953-a34a8272ec90",
  "intent": "research",
  "requested_agent": "hermes",
  "objective": "Investigate the claim in the supplied source",
  "context_refs": ["content_abc"],
  "reply_target_ref": "reply_123"
}
```

The principal comes from authenticated user identity or authorized service delegation resolved through the source event. Validate ownership of all context and reply references. Event-less MCP/direct tasks derive ownership from the caller and need not have a reply target.

```json
{
  "schema_version": "1",
  "task_id": "70d82b26-1b1f-459b-93e1-a3c772663488",
  "status": "QUEUED",
  "assigned_agent": null
}
```

Exact optional fields and response status codes will be finalized in the schema/API task. An accepted response means durable local acceptance, not remote execution success.

### Control plane to gateway: `POST /v1/deliveries`

```json
{
  "schema_version": "1",
  "task_id": "70d82b26-1b1f-459b-93e1-a3c772663488",
  "idempotency_key": "70d82b26-1b1f-459b-93e1-a3c772663488:completion:1",
  "channel": "telegram",
  "reply_target": {
    "channel_id": "chat_42",
    "message_id": "msg_7"
  },
  "text": "Research completed. The result is available through your task reference.",
  "artifacts": []
}
```

Proposed receipt: `{ "delivery_id": "gateway_delivery_123", "status": "ACCEPTED" }`. The gateway receipt is not proof of a successful channel reply. Agree a final acknowledgement or authenticated delivery-status lookup, plus stable idempotency behavior, before delivery retries are implemented.

## Lifecycle and remote guarantees to confirm

Proposed public task states: `RECEIVED`, `REJECTED`, `QUEUED`, `AWAITING_APPROVAL`, `DISPATCHING`, `RETRY_PENDING`, `RUNNING`, `UNKNOWN`, `SUCCEEDED`, `FAILED`, `CANCELLED`.

- Gate sensitive work before dispatch, including actions requested through MCP.
- Persist a stable dispatch correlation key before the remote call.
- `RUNNING` requires confirmed remote acceptance and an owner/reference; local queueing is insufficient.
- `UNKNOWN` means remote execution cannot currently be determined, including accepted-but-response-lost cases. It is not confirmed failure.
- `RETRY_PENDING` is for classified retryable failures with a safe retry path; a timeout alone does not establish that path.
- Retry side-effecting dispatch only under a documented remote idempotency guarantee or conclusive reconciliation showing no accepted work. Otherwise require operator reconciliation.
- A remote cancellation request is not confirmed cancellation. Unsupported cancellation must be returned explicitly.
- Completion means a verified remote result/reference. Delivery has its own status and can fail independently.
- Routine creation requires confirmed scheduling support and a remote schedule reference; XMUX does not take over its execution.

The complete legal transition graph, retry budgets, error taxonomy and concurrent-update rules belong to subsequent contract tasks.

## MCP inventory to confirm

`capture_content`, `create_task`, `delegate_task`, `get_task_status`, `get_result`, `list_agents`, `approve_action`, `list_routines`, `create_routine`, `search_references`.

`create_task` and `delegate_task` must use the same core intake and scoped idempotency behavior. Proposed distinction: create accepts a task intent; delegate also expresses destination/capability selection. Neither bypasses authorization or approval.

Reference search covers authorized XMUX references. Remote memory lookup is exposed only when its owning adapter supports it. No client compatibility or remote authentication support is claimed until tested with an actual configured client.

## Dependencies requiring an owner answer

| Question                                                                                    | Needed from                           | Blocks                                            |
| ------------------------------------------------------------------------------------------- | ------------------------------------- | ------------------------------------------------- |
| How are verified identity mappings provisioned and resolved?                                | Gateway + control plane               | Privileged intake and cross-channel authorization |
| Is an event ID globally namespaced across bot accounts/channels?                            | Gateway                               | Reliable event deduplication                      |
| Does dispatch accept a stable idempotency/correlation key? What is its scope/lifetime?      | Multiplexer                           | Automated safe dispatch retries                   |
| Can accepted jobs be found by correlation key after a lost response? Is absence conclusive? | Multiplexer                           | Recovery after ambiguous dispatch                 |
| Which installed adapters actually support status, callbacks, cancellation and schedules?    | Multiplexer                           | Supported tracking/recovery operations            |
| Does delivery deduplicate? How is final delivery queried/acknowledged?                      | Gateway                               | Safe result delivery retries                      |
| How are service credentials and user delegation claims validated?                           | All                                   | Service boundary authentication                   |
| Which OAuth issuer, audiences, scopes and identity links will remote MCP use?               | Control plane + account owner         | Authorized real-client verification               |
| What are approval expiry and content/result retention policies?                             | Product/account owner + control plane | Policy and retention defaults                     |

Language/framework selection can be discussed alongside this workshop, but is not considered settled by these payload examples.

## Review record and next step

| Participant         | Review status       |
| ------------------- | ------------------- |
| Control-plane owner | Pending user review |
| Gateway owner       | Pending team review |
| Multiplexer owner   | Pending team review |

Preparation is complete when the agenda, discrepancy proposals and sample payloads are reviewable. The workshop itself and team agreement are still pending; do not mark them complete based on this document existing.

After review, record accepted/rejected decisions and unresolved owners here. Then proceed to P0.2: executable versioned schemas and OpenAPI definitions for the seven required shared contracts. Do not freeze production wire contracts before the affected owners agree.
