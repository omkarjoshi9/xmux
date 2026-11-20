# XMUX shared contracts — draft v1

P0.2–P0.3 provide thirteen language-neutral wire schemas and a generated OpenAPI component catalog. These are executable **proposals pending gateway, multiplexer and control-plane agreement**, not stable or deployed APIs.

## Contents

| Contract          | Meaning                                                              |
| ----------------- | -------------------------------------------------------------------- |
| `EventEnvelope`   | Verified gateway event, source identity mapping and reply context    |
| `TaskRequest`     | Caller intent, idempotency key and authorized context references     |
| `RemoteTaskRef`   | Confirmed remote acceptance and owning agent/correlation identifiers |
| `TaskStatus`      | Coordination state, owner, timestamps, error and result references   |
| `DeliveryRequest` | Idempotent outbound reply request to the gateway                     |
| `ApprovalRequest` | Approve/deny decision bound to a persisted action fingerprint        |
| `RoutineRef`      | Confirmed remote schedule reference and owning assistant             |

P0.3 adds the following remote lifecycle contracts:

| Contract                | Meaning                                                                             |
| ----------------------- | ----------------------------------------------------------------------------------- |
| `ErrorEnvelope`         | Bounded sanitized error, correlation and optional retry-delay hint                  |
| `AgentCapabilities`     | Explicit installed adapter support flags and dispatch idempotency guarantees        |
| `RemoteResult`          | Confirmed success, failure or cancellation observation                              |
| `TaskCallback`          | Deduplicatable remote status/result notification                                    |
| `CancellationReceipt`   | Requested, confirmed, unsupported, unknown or already-terminal cancellation outcome |
| `ReconciliationOutcome` | Found, authoritative absence, unknown or unsupported dispatch lookup                |

`schemas/v1/Common.schema.json` holds shared UUID, UTC timestamp, reference, attachment, reply-target and state definitions. Each other file is a standalone root schema referencing that resource. Register all schemas before validating. Resource identifiers under `https://xmux.example/` are offline identifiers, not network endpoints.

## Run locally

Requires Node.js 22 or newer. Package-local npm tooling is a temporary validation harness; it does not select the team's backend framework or workspace manager.

```bash
cd packages/contracts
npm ci
npm run generate
npm run check:generated
npm test
```

Edit source JSON Schemas, then regenerate `openapi.json`. Do not edit the generated file. The committed lockfile pins validation dependencies. The schemas use [JSON Schema draft 2020-12 as supported by Ajv](https://ajv.js.org/json-schema.html); the catalog uses [OpenAPI 3.1.1](https://spec.openapis.org/oas/v3.1.1.html).

The OpenAPI document intentionally has empty `paths`: this task defines reusable schemas, not service operations, transport authentication, status codes or HTTP response mappings. Those contracts require the subsequent P0 tasks. No dispatch or delivery endpoint is implemented here.

## Draft conventions

- `schema_version` is the string `"1"`; schema/package revision is independently `0.1.0-draft.2`. No compatibility promise is made until team approval.
- Objects reject undeclared fields, including nested auth/reply structures. New fields require coordinated producer/consumer updates; do not assume additive changes are automatically compatible.
- XMUX IDs are UUIDs; remote/platform references are opaque nonempty strings. UTC timestamps use an explicit `Z` suffix.
- Task states are uppercase, including approval, retry and unknown states. State legality, timestamp ordering and remote-reference requirements for particular transitions are enforced by later lifecycle services, not this structural schema.
- Direct tasks may omit `source_event_id` and `reply_target_ref`; missing optional fields must be omitted rather than sent as null. Status owner/error fields use explicit null when unavailable.
- `ApprovalRequest` is a decision submission, not an approval-record creation request. The server resolves the approver from authentication and verifies the stored action fingerprint, ownership, expiry and policy.
- `RoutineRef` represents an already confirmed remote schedule. `UNKNOWN` distinguishes an unreachable owner from a confirmed `REMOVED` schedule. Actual schedule status mapping must be agreed with adapters.
- `DeliveryRequest.artifacts` and task results carry authorized opaque references. Their resolution/formatting and final delivery acknowledgement belong to later contracts.
- Input limits (65,536-character text, 16,384-character objective, up to 100 references/attachments, and per-field lengths in schemas) are provisional review choices; service request-byte limits are separate.

## Trust boundaries and pending agreement

Schema validity does not authenticate a request, validate a permission, make a URL safe to fetch, or prove content access. Source text remains data. The receiving service must authenticate the gateway, resolve `identity_mapping_ref`, verify tenant membership and check ownership of every referenced object. Schema rejection of an injected permission field is only structural validation, not a security integration test.

Before these drafts are frozen, confirm identity mapping provision/lookup, tenant scope, platform event namespacing, remote dispatch idempotency and reconciliation guarantees, delivery receipts, approval fingerprint canonicalization, routine state mapping, and optional agent capabilities. Authenticated clients may not choose arbitrary principals or approvers in task/approval bodies.

The event wire field is `platform_event_id`; the gateway must namespace it across bot accounts/chats when necessary. Event deduplication, payload fingerprint conflicts, user isolation, remote execution safety and content retention remain service responsibilities and later P0 contracts.

Local workshop notes and implementation plans stay in ignored `plan/`; they are not dependencies of this package. The complete error taxonomy, transport/security conventions, idempotency policy and shared cross-team fixture scenarios remain subsequent P0 tasks.

## Remote lifecycle semantics (P0.3)

All six new contracts are proposals. Discriminated variants reject contradictory fields: for example a requested cancellation cannot carry a confirmation timestamp, a successful result cannot also carry an error, and an unknown reconciliation cannot claim a found remote job.

### Capabilities and multiplexer compatibility

`AgentCapabilities` reuses the existing multiplexer vocabulary: `operations`, `supports_status`, `supports_cancel`, `supports_schedule`, and `supports_memory_lookup`. The shared manifest adds `agent_id`, an explicit version, `supports_callbacks`, `supports_correlation_lookup`, and a structured `dispatch_idempotency` declaration. All flags are required booleans with no defaults; only advertise features the installed adapter can actually perform. Operations are unique opaque capability names such as `research` or `coding`, not an invented fixed routing taxonomy.

The Python multiplexer branches inspected during this task already map TaskRequest and RemoteTaskRef. Their internal `completed` state must map to shared `SUCCEEDED`. New manifest flags need explicit adapter mappings; missing capabilities must not be fabricated as supported. The team must agree on scope and retention before advertising dispatch idempotency. The proposed scope is `(principal, agent, operation)` plus the stable idempotency key; guarantees expire after the declared retention window. Service authorization and registry filtering remain necessary even when an operation appears here.

### Results and callbacks

`RemoteResult` carries one nested `RemoteTaskRef` so the owner, task and dispatch correlation travel together. Success requires at least one authorized result reference; an adapter with only inline output must persist/reference that output through the agreed result storage path. Failure carries a sanitized ErrorEnvelope; cancellation is terminal confirmation. Adapter result storage and this nonempty-success-reference policy require team agreement.

`TaskCallback` carries a stable callback UUID, emission time and nullable sequence. Status notifications cover RUNNING and UNKNOWN; terminal notifications use RemoteResult. Callback authentication/signatures belong in transport headers, not JSON fields. The receiver must authenticate the sending service and authorize it for the remote owner; verify every task/agent/correlation association; deduplicate callbacks; and enforce monotonic terminal state. Sequence, when supplied, is monotonic per remote job, not global. Null means ordering is unavailable; neither emission time nor payload validity proves freshness. Replay-window and signature details are later security-contract work.

### Cancellation

REQUESTED acknowledges a cancellation request only. CONFIRMED includes confirmation time. UNSUPPORTED and UNKNOWN include explicit errors and must not mark a task cancelled. ALREADY_TERMINAL identifies the observed terminal state; the coordinator must reconcile that state and obtain result/error details where required. A receipt does not guarantee that side effects were rolled back. `cancellation_id` identifies the stable logical cancellation attempt for deduplication; retry behavior still depends on the adapter's cancellation interface.

### Reconciliation and safe recovery

FOUND contains the remote task reference. The receiver must compare its task, owner and correlation IDs with the lookup identity; JSON Schema cannot enforce those cross-field equalities. ABSENT requires an authoritative lookup evidence reference and the dispatch start time it covers. An eventually consistent empty search is UNKNOWN, not ABSENT. An unavailable lookup is UNKNOWN; no lookup capability is UNSUPPORTED.

An authoritative flag in JSON is a claim, not proof. The service must verify the authenticated adapter's guarantee, evidence scope, principal ownership, dispatch attempt and coverage, and ensure the old in-flight request cannot later be accepted before using absence to redispatch. Lookup retention expiry invalidates negative evidence. If the adapter cannot establish that guarantee, leave the task unknown and require reconciliation/operator review. No payload includes a `safe_to_retry` shortcut.

### Errors and remaining policy

Error codes are bounded lowercase identifiers for now; the complete allowlist, HTTP mapping and field-validation detail format belong to P0.4. `retry_after_seconds` is a delay hint or null, never permission to retry a side effect. Producers must sanitize `message` before serialization; schema limits cannot detect credentials embedded in a string. Raw exception details, stack traces and arbitrary provider payload bags are not accepted.

These tests establish structural invariants and parity between standalone and bundled schemas. They do not establish callback authentication, user isolation, actual remote capability, safe retry execution or lifecycle correctness; those need the later services and integration tests.
