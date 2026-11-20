# XMUX shared contracts — draft v1

P0.2–P0.4 provide language-neutral wire schemas, HTTP conventions and a generated OpenAPI component catalog. These are executable **proposals pending gateway, multiplexer and control-plane agreement**, not stable or deployed APIs.

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

The OpenAPI document intentionally has empty `paths`: it defines reusable schemas, parameters, headers and error responses. Service operations and transport authentication bindings remain future work. No dispatch or delivery endpoint is implemented here.

## Draft conventions

- `schema_version` is the string `"1"`; schema/package revision is independently `0.1.0-draft.3`. No compatibility promise is made until team approval.
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

Local workshop notes and implementation plans stay in ignored `plan/`; they are not dependencies of this package. Identity/security policy, idempotency semantics and shared cross-team fixture scenarios remain subsequent P0 tasks.

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

Error codes now use the P0.4 closed allowlist, HTTP mapping and structured validation details below. `retry_after_seconds` is a delay hint or null, never permission to retry a side effect. Producers must sanitize `message` before serialization; schema limits cannot detect credentials embedded in a string. Raw exception details, stack traces and arbitrary provider payload bags are not accepted.

These tests establish structural invariants and parity between standalone and bundled schemas. They do not establish callback authentication, user isolation, actual remote capability, safe retry execution or lifecycle correctness; those need the later services and integration tests.

## HTTP and validation conventions (P0.4)

The source of HTTP code mappings is `conventions/http.json`; `ErrorCode.schema.json` contains the matching closed vocabulary, with tests detecting drift. The generator creates reusable OpenAPI error responses, query parameters and headers. These describe proposed service behavior; no HTTP handler is implemented yet. The mappings follow [HTTP semantics](https://www.rfc-editor.org/rfc/rfc9110.html), while the exact XMUX code names are project choices.

### Validation and failure precedence

1. Enforce transport framing, supported content type and configured byte limit before expensive parsing: `invalid_request` (400), `unsupported_media_type` (415), or `payload_too_large` (413).
2. Authenticate and authorize the operation before returning detailed body diagnostics. Missing/invalid caller authentication is `unauthenticated` (401), with a configured `WWW-Authenticate` challenge; disallowed operations are `forbidden` (403). Hidden resources use the same `not_found` (404) response as missing resources.
3. Reject malformed JSON as `malformed_json` (400). Bodies must be objects where the operation requires one. No duplicate JSON member names are accepted; the eventual parser must detect them and return `invalid_request` (400).
4. For versioned bodies, a present string version other than `"1"` returns `unsupported_schema_version` (400) before version-specific validation. A missing or wrongly typed version is `validation_failed` (422), like other shape violations. No fallback to a different version and no type coercion.
5. Validate the supported-version schema. Return `validation_failed` (422) with 1–50 unique `violations`; each contains a schema-controlled `path` and a bounded `reason`. Omit submitted values, raw validator errors and provider exception details. Sort by path then reason before truncation. A path uses [JSON Pointer](https://www.rfc-editor.org/rfc/rfc6901.html), with the empty string for root. Unknown property names may contain secrets: point to their nearest declared ancestor rather than echoing the name. Message text must use sanitized templates.
6. Apply resource ownership, reference access, lifecycle and capability checks. A schema-valid identity/reference is not evidence of permission. No dispatch can happen before all checks pass.

Header correlation selection precedes response creation; it does not bypass auth. A malformed correlation header is reported only after authentication/operation authorization succeeds; otherwise return the authentication/authorization error with a fresh server-generated correlation UUID. The exact middleware ordering for ingress limits and auth is implementation work; no detailed body diagnostics should leak to unauthenticated callers.

| HTTP | Codes                                                                                                                                   |
| ---- | --------------------------------------------------------------------------------------------------------------------------------------- |
| 400  | `malformed_json`, `unsupported_schema_version`, `invalid_request`, `invalid_cursor`                                                     |
| 401  | `unauthenticated`                                                                                                                       |
| 403  | `forbidden`                                                                                                                             |
| 404  | `not_found`                                                                                                                             |
| 409  | `idempotency_conflict`, `invalid_state_transition`, `approval_required`, `approval_expired`, `result_pending`, `remote_outcome_unknown` |
| 413  | `payload_too_large`                                                                                                                     |
| 415  | `unsupported_media_type`                                                                                                                |
| 422  | `validation_failed`, `unsupported_capability`, `cancellation_unsupported`, `content_unavailable`                                        |
| 429  | `rate_limited`                                                                                                                          |
| 500  | `internal_error`                                                                                                                        |
| 502  | `auth_failed`, `remote_failure`                                                                                                         |
| 503  | `unavailable`, `remote_unavailable`                                                                                                     |

These statuses apply when the HTTP operation itself fails. A successful task-status request remains HTTP 200 even if the remote task failed. `auth_failed` describes destination credentials; it must not be confused with caller authentication. Error bodies always use ErrorEnvelope v1, even when rejecting another requested version. Unknown provider codes normalize to the closest supported code; raw upstream text is not forwarded. Detailed retry and idempotency behavior remains P0.6/P0.7 work.

`violations` is required only for `validation_failed` and forbidden on other error codes. `retry_after_seconds` remains required and nullable. On 429/503, an optional `Retry-After` response uses integer seconds matching a non-null body hint; omit it when no delay is known. The hint is not retry authorization. Runtime code must enforce matching header/body values.

### Cursor pagination

`TaskListQuery` models the normalized query for `GET /v1/tasks`: optional `limit` (default 20, range 1–100), `cursor`, and uppercase `status`. The HTTP parser accepts a single decimal integer for limit and rejects duplicate parameters, unknown fields, signs/fractions and invalid values as `invalid_request` (400). Schema defaults are annotations: the handler must apply 20 explicitly. There is no offset or client-selected principal.

`TaskPage` contains `schema_version`, up to 100 TaskStatus items, and `next_cursor` (opaque token or explicit null). The handler must also enforce `items.length <= requested limit` and stop emitting a continuation once no more visible items remain. Empty pages are valid. No total count is exposed.

Proposed ordering is `(created_at DESC, task_id DESC)` using a stable UUID tie-breaker. The cursor represents the last returned key and initial query upper bound. The server authenticates/signs or encrypts cursor state and binds it to tenant, principal, filters, effective limit, ordering, upper bound and expiry. Recheck access on every page. A malformed, tampered, expired or mismatched cursor returns the same `invalid_cursor` error without revealing its internals. The URL-safe syntax schema alone cannot verify any of those properties. Cursor encoding, key rotation and TTL require runtime decisions.

Pagination is a live authorized view bounded by the initial creation high-water mark, not a database snapshot. New tasks above that boundary do not enter later pages; existing tasks can change status or visibility, so omissions across a changing filter are possible. Do not promise snapshot consistency or use cursor data as authorization.

### IDs, timestamps and correlation

XMUX identifiers use hyphenated UUID strings. Both hex cases are accepted; services emit lowercase canonical form. No specific UUID generation version is imposed yet. Platform IDs, agent IDs and remote references remain opaque strings and must not be coerced to numeric IDs or UUIDs.

Timestamps must be valid UTC date-times with a literal `T` and `Z`, optionally fractional seconds. Emit millisecond precision by convention. Reject offsets (including `+00:00`) on the shared wire, local timestamps and invalid calendar dates; normalize provider times in the owning adapter. Occurrence time is source provenance, received/created time is server-owned, and clock skew is not authorization evidence. Chronological consistency and clock policy require service checks.

`X-Correlation-ID` is an optional single UUID request header and a response header on success/errors. Header names are case-insensitive. Reuse a valid supplied UUID in canonical lowercase; generate a fresh UUID when absent. Reject duplicate, comma-joined or malformed values with `invalid_request` once auth permits that diagnostic; use a fresh server-generated UUID for the error response. Do not log or echo the invalid raw header. For HTTP errors, the response header equals the outer ErrorEnvelope `correlation_id`.

This HTTP request correlation is diagnostic only: callers can reuse it, so it cannot deduplicate work or authorize access. RemoteTaskRef's correlation identifies a persisted dispatch attempt and must remain stable across its reconciliation. Do not overwrite remote correlation fields with an unrelated HTTP polling request ID. Nested remote errors can retain their own upstream correlation; outer HTTP failures use the current request's correlation.

### Validation limits

Tests cover schema acceptance/rejection, error-code/status consistency, generated OpenAPI validity, pagination bounds and header syntax. They do not implement duplicate-JSON-key detection, middleware ordering, cursor signing/ownership, field-error sanitization, chronological checks, or header/body matching. Those are explicit requirements for the API implementation.
