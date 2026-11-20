# XMUX shared contracts — draft v1

P0.2–P0.7 provide language-neutral wire schemas, HTTP conventions and a generated OpenAPI component catalog. These are executable **proposals pending gateway, multiplexer and control-plane agreement**, not stable or deployed APIs.

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

- `schema_version` is the string `"1"`; schema/package revision is independently `0.1.0-draft.6`. No compatibility promise is made until team approval.
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

Local workshop notes and implementation plans stay in ignored `plan/`; they are not dependencies of this package. Credential implementation, adapter guarantee agreement and shared cross-team fixture scenarios remain subsequent work.

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

FOUND contains the remote task reference. The receiver must compare its task, owner and correlation IDs with the lookup identity; JSON Schema cannot enforce those cross-field equalities. ABSENT requires an authoritative lookup evidence reference, the covered dispatch start time, evidence expiry, and explicit claims that the attempt was never accepted and its in-flight acceptance path is closed. An eventually consistent empty search is UNKNOWN, not ABSENT. An unavailable lookup is UNKNOWN; no lookup capability is UNSUPPORTED.

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

| HTTP | Codes                                                                                                                                                          |
| ---- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 400  | `malformed_json`, `unsupported_schema_version`, `invalid_request`, `invalid_cursor`                                                                            |
| 401  | `unauthenticated`                                                                                                                                              |
| 403  | `forbidden`                                                                                                                                                    |
| 404  | `not_found`                                                                                                                                                    |
| 409  | `idempotency_conflict`, `request_in_progress`, `invalid_state_transition`, `approval_required`, `approval_expired`, `result_pending`, `remote_outcome_unknown` |
| 413  | `payload_too_large`                                                                                                                                            |
| 415  | `unsupported_media_type`                                                                                                                                       |
| 422  | `validation_failed`, `unsupported_capability`, `cancellation_unsupported`, `content_unavailable`                                                               |
| 429  | `rate_limited`                                                                                                                                                 |
| 500  | `internal_error`                                                                                                                                               |
| 502  | `auth_failed`, `remote_failure`                                                                                                                                |
| 503  | `unavailable`, `remote_unavailable`                                                                                                                            |

These statuses apply when the HTTP operation itself fails. A successful task-status request remains HTTP 200 even if the remote task failed. `auth_failed` describes destination credentials; it must not be confused with caller authentication. Error bodies always use ErrorEnvelope v1, even when rejecting another requested version. Unknown provider codes normalize to the closest supported code; raw upstream text is not forwarded. Local idempotency is defined in P0.6 below; remote recovery is specified in P0.7 below, pending adapter agreement.

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

## Authenticated actors and effective principals (P0.5)

`AuthenticatedActor`, `IdentityMapping` and `DelegationContext` are **internal** records. They are generated or loaded by trusted server components after verification. They are not public request-body alternatives, self-authenticating JSON, or token formats. `AuthorizationScope` defines the proposed operation-grant vocabulary; `conventions/authorization.json` maps operations to permitted actor classes, service roles, required scopes and principal sources. These are reviewable policy inputs, not an authorization engine.

### Who called versus whose work

- A USER actor is derived from an authenticated issuer/subject, the server's principal mapping, and verified tenant membership. It has one selected tenant and explicit user scopes. An OAuth client identity alone is not an end-user principal.
- A SERVICE actor identifies the registered calling service, its server-assigned role, exact destination audience, allowed tenants and operation scopes. Its JSON cannot include an effective `principal_id`. Service registration and grants are configured by trusted operators; a caller cannot choose its role from request data.
- The effective principal comes from a verified user context, active channel mapping, or an existing authorized task/remote-job record as specified for the operation. Service credentials alone never authorize impersonation.
- Effective permissions are the intersection of authenticated caller grants, registered service permissions, principal/tenant permissions, task action constraints, destination capability and any required approval. Neither user intent nor captured content expands that intersection.
- Unknown operations, missing scope, mismatched actor role/audience, inactive membership or missing required identity/delegation evidence deny by default. Grant arrays may be empty; this grants no operations. Wildcards are unsupported in this draft.

Token verification must check trusted issuer, intended audience, integrity, expiry/not-before and applicable revocation, using the configured credential mechanism. Opaque credentials require authoritative verification/introspection rather than merely decoding them. The issuer, OAuth deployment, service credential format, expiry/skew limits and key rotation remain implementation decisions. The design must follow [OAuth security best current practice](https://www.rfc-editor.org/rfc/rfc9700.html) when OAuth is used. Schemas check field shape only, not token validity or chronological ordering.

### Gateway event resolution

For `event.ingest`, require a gateway SERVICE actor with `events:ingest`. Resolve `auth_context.identity_mapping_ref` from the authoritative identity store. Require an ACTIVE mapping whose `gateway_service_id`, platform, external actor and tenant match the authenticated gateway and verified event; the tenant must also be in the service's allowed tenants. Verify principal membership is active. Persist the resolved principal and tenant on the event/task with the mapping reference used for audit.

The platform actor is obtained from verified platform metadata, never message text. The mapping must be created by explicit account linking with proof of control and user consent. A username, matching display name, email claim in a post, or guessed mapping ID cannot establish ownership. Cross-platform accounts are separate verified mapping records that may resolve to the same principal. REVOKED mappings cannot authorize new intake or work. An unlinked actor has no privileged execution path; record a sanitized rejection or use a separately designed linking flow, without creating a runnable task.

Mapping references are lookup keys, not bearer credentials. A valid-looking event with a forged tenant or mapping remains unauthorized. Repeated events must be ownership-checked before returning a previous task reference. Do not expose another actor's mapping existence through error details.

### Direct requests, MCP and gateway-created tasks

TaskRequest and ApprovalRequest keep identity out of public payloads. A direct HTTP/MCP request obtains its USER actor from authentication. Reject caller-injected principal, tenant, role, scope and internal-context fields rather than stripping and trusting them.

A gateway SERVICE may create a task only from an existing event it is authorized to reference. Derive the effective principal/tenant from that event and recheck the active mapping and current principal permissions. Event-less service-created tasks are denied in this draft. Direct user tasks may omit the event, but every supplied context, event and reply reference must belong to the effective user/tenant.

The MCP server must preserve validated end-user identity when invoking core services. In-process calls use the verifier-built user context. A separate service boundary needs a correctly audience-bound delegated credential or authoritative verification flow; copying identity headers or reusing a token intended for another service is not sufficient. MCP client registration and tool parameters cannot nominate an arbitrary owner.

The operation matrix defines entrypoint requirements, not every action-specific permission. For example `tasks:create` with schedule intent also requires the effective principal's `routines:create` permission; a code-changing task also requires approval for its exact action. A gateway-origin schedule can proceed through the authorized event/task/dispatch flow with those checks, even though direct routine API calls require a USER actor. A user access token expiring after accepted intake does not by itself erase durable work: workers use persisted authorization evidence and recheck current principal policy before sensitive dispatch.

### Bounded delegation to the multiplexer

An orchestrator SERVICE with `tasks:dispatch` can request a delegation only after verifying persisted task ownership, action-specific permissions and applicable approvals. Create a DelegationContext containing the service actor, exact multiplexer audience, principal/tenant, task ID/version, selected agent, action fingerprint, approval references and bounded validity window. The grant ID identifies the persisted authorization decision; the grant is integrity-protected or retrieved through an authenticated authoritative lookup. Do not place unsigned client-provided contexts directly into adapter calls.

The multiplexer verifies the authenticated service matches `actor_service_id`, the audience and validity window, grant revocation, principal/tenant/task/agent bindings and task version. It resolves approval references to valid decisions for the same action fingerprint. Empty approval references are permitted only when policy does not require approval; the array itself is never proof that approval is unnecessary. Material changes to objective, repository, branch, allowed tools, destination or action permissions require reauthorization and a fresh matching grant. Request fingerprints and local replay semantics are defined in P0.6 below. Approval action snapshots require their own explicit canonicalization profile before grant issuance is implemented.

The existing multiplexer `DispatchContext` already separates trusted principal/tenant values from task payloads and checks coding scope. The shared DelegationContext is a proposal for the boundary that constructs that internal context; do not bypass its repository/branch/tool checks. Map coding permissions from approved server-side task state, never arbitrary captured JSON. No multiplexer implementation is changed by this task.

### Ownership and approval policy

| Operation                                               | Identity basis and additional checks                                                                                              |
| ------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| Task list/status/result, routine list, reference search | Authenticated user; tenant and owner filter before data access; validate every referenced artifact                                |
| Agent list                                              | User-visible configured agents; orchestrator discovery must be constrained to an authorized persisted task's owner                |
| Task cancellation                                       | Owning user with `tasks:cancel`; remote cancellation capability still applies                                                     |
| Approval decision                                       | USER actor with `approvals:decide`, matching task owner, exact stored action/version and unexpired approval                       |
| Routine creation                                        | Owning user or authorized task workflow; scheduling capability and action policy still apply                                      |
| Remote callbacks/observations                           | Registered multiplexer SERVICE with `tasks:observe`; bind to persisted remote job and expected agent/task/correlation             |
| Outbound delivery                                       | Orchestrator SERVICE with `deliveries:submit`; use the persisted task's authorized reply target, not a newly supplied destination |

A service cannot approve its own dispatch. Direct channel-message approval forwarding is not enabled by these drafts; it needs an explicit verified-user approval flow before support is advertised. The minimum approval path is an authenticated user/MCP decision. Do not interpret a literal “approved” in captured text as approval.

Return `unauthenticated` for invalid credentials, `forbidden` for a disallowed operation, and `not_found` for missing or inaccessible owned resources. Avoid differences that disclose another user's records. Audit actor type/ID, effective principal/tenant, operation, task/grant/mapping reference, decision/reason and correlation ID. Do not audit credentials, source bodies, complete tokens or unrestricted artifact contents.

### Pending agreement and test boundary

The gateway owner must confirm authoritative identity provisioning/resolution and revocation behavior. The multiplexer owner must confirm delegation verification and coding-scope mapping. The team must select credential providers, audiences, scope issuance, grant lifetimes, clock skew and revocation enforcement. No credential/token scheme is invented as already available here.

Tests verify actor variants, scope restrictions, mapping states, delegation shape, operation/scope consistency, and rejection of identity fields in public inputs. They are structural contract tests, not proof of signature validation, user isolation, privilege intersection, approval enforcement or revocation. Add those service integration tests when the identity/authorization implementation exists.

## Idempotency and request fingerprints (P0.6)

`conventions/idempotency.json` specifies scope, schemas and fingerprint projections. `IdempotencyScope`, `RequestFingerprint` and `IdempotencyRecord` describe internal coordination metadata, not public authority or a complete database design. `scripts/reference-fingerprint.mjs` is a tested interoperability reference using development dependencies; it is not a production request handler or deduplication store.

### Scope and key ownership

For task creation, approval decisions and delivery submission, the lookup tuple is `(tenant_id, principal_id, operation, idempotency_key)`. Resolve tenant/principal through P0.5 before lookup; the server selects the operation, not a client field. Store tuple columns or an unambiguous structured encoding, never delimiter-concatenated values. The caller supplies a nonempty key up to 255 characters. Keys are case-sensitive opaque strings; do not trim or normalize them, and do not log raw keys. Use a fresh key for a genuinely new action and reuse the same key for retries of that action.

The authenticated service/client ID is intentionally not part of the tuple: switching an authorized transport must not duplicate the same principal's logical action. `create_task` and `delegate_task` both map to `task.create` for the same TaskRequest. The destination, objective and source/context/reply references are included in the request fingerprint, so changing any of them under the same key conflicts. Approval scope covers decision and action fingerprint; changing approve to deny under one key conflicts. A second key cannot bypass an already-decided approval: lifecycle checks still apply.

Source events use the existing global `(platform, platform_event_id)` lookup instead. The gateway namespaces event IDs across bot accounts/channels when needed. Resolve and compare owner/tenant before returning an existing event/task. A source-key collision across owners must fail without revealing another owner's record, even though the database uniqueness key has no tenant column. Do not create a second task on that collision.

### Fingerprint profile

The profile is `xmux-jcs-sha256-v1`. After strict schema validation, form this object and canonicalize it with [RFC 8785 JSON Canonicalization Scheme](https://www.rfc-editor.org/rfc/rfc8785.html), then SHA-256 hash its UTF-8 bytes and encode the digest as 64 lowercase hexadecimal characters:

```text
{
  profile: "xmux-jcs-sha256-v1",
  operation: <server-selected operation>,
  tenant_id: <verified lowercase UUID>,
  principal_id: <verified lowercase UUID>,
  payload: <projected validated request>
}
```

Projection rules:

- TaskRequest, ApprovalRequest and DeliveryRequest exclude only their top-level `idempotency_key`. The key belongs in the lookup tuple, not the payload digest.
- EventEnvelope excludes generated `event_id` and `auth_context`. The mapping reference must still be verified on every request before comparison; changing it cannot change ownership. Source actor, platform/event ID, occurrence time, source content, attachments and reply target remain included.
- Normalize only declared UUID fields to lowercase: trusted identity UUIDs; TaskRequest.source_event_id; ApprovalRequest.approval_id/task_id; DeliveryRequest.task_id; EventEnvelope.tenant_id. Do not lowercase opaque references or text.
- Preserve strings and array order exactly. Do not trim text, normalize Unicode, reorder URLs/attachments/references, fill optional defaults, or treat missing and null as equivalent. Null is rejected wherever the request schema forbids it. UUIDs are the only subtype normalization in this profile; timestamp spellings otherwise remain significant.
- Reject duplicate JSON keys at the parser, malformed Unicode and non-JSON values before hashing. The reference helper receives already-parsed objects, so it cannot detect duplicate keys lost by an earlier parser. It rejects undefined, non-finite values and unsupported object shapes instead of silently dropping them.
- Never fingerprint transport headers, credentials or freshly generated processing timestamps. The request's declared source timestamp is stable provenance and stays included.

Store the profile with the digest and do not reinterpret old records using a changed algorithm. The digest is not an authentication mechanism, an anonymization guarantee, or the approval `action_fingerprint`. It can expose information about guessable inputs; protect it as internal coordination data and do not echo stored/requested hashes in conflict responses.

Context, content and artifact references must identify immutable/versioned snapshots for the operation lifetime. A mutable reference pointing to changed content can preserve a request digest while changing the work; resolve/pin its version at first acceptance and reuse that snapshot. Do not refetch mutable content on replay to create new execution. The exact approval action snapshot (repository, branch, tools, destination and constraints) needs its own profile when approvals are implemented; this task does not claim request equality is approval equivalence.

### Local outcomes and concurrency

| Authorized lookup outcome              | Required behavior                                                                                                                             |
| -------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| No existing key                        | Atomically reserve the tuple and fingerprint; create the coordination resource and outbox notification in the same transaction where possible |
| Same key and fingerprint, ACCEPTED     | Return the original stable resource reference with caller-visible current state; do not dispatch, enqueue or deliver a second action          |
| Same key and fingerprint, RESERVED     | Bounded wait for the winning transaction, or HTTP 409 `request_in_progress`; never treat an unresolved reservation as a new request           |
| Same key, different fingerprint        | HTTP 409 `idempotency_conflict`; sanitized message, no original payload/hash/resource details and no mutation of the old record               |
| RETIRED record                         | Do not recreate work; return a sanitized resource-unavailable/not-found result after authorization                                            |
| Invalid input or unauthorized identity | Reject without creating a usable reservation or exposing a previous resource                                                                  |

A uniqueness constraint plus transactional conflict handling must choose one winner across concurrent requests. The winning transaction ties the scope/fingerprint to the resource and durable outbox. A process dying before commit leaves no accepted task; a process dying after commit must recover that same task. If a separate committed RESERVED state exists, reconciliation must establish whether its resource/outbox already exists before resuming it. Lease expiry alone cannot authorize remote execution.

The task/event creation endpoints should return HTTP 202 for new durable acceptance and HTTP 200 for an accepted replay. Approval decisions return HTTP 200 once persisted, including authorized replays; delivery submission returns HTTP 202 for local acceptance and HTTP 200 for replay of that receipt. These are draft endpoint semantics; concrete success response envelopes remain pending API work. Replay responses may reflect newer status and refreshed HTTP correlation IDs, but stable resource identity must not change.

Every replay rechecks present caller authentication, ownership and reference visibility before disclosing the stored result. An accepted approval replay reports the recorded decision without reapplying it; a newly requested decision still checks current expiry/action policy. A lost response, a new HTTP correlation ID, a restarted worker or a failed remote task does not create a new logical request.

### Event replay and retention

The event UUID may differ on a repeated gateway attempt, but the original source-event identity and task references are retained. The source occurrence timestamp and extracted references must be stable across retries. Changed content under the same source-event key conflicts; a platform edit is a separately identified event/revision, not a silent overwrite or automatic rerun. The gateway must agree on stable event/revision identifiers and snapshot references.

Do not automatically expire unresolved reservations or records for running, unknown or pending-delivery work. On resource deletion, retire the key rather than silently allowing it to create the action again. Minimal tombstones retain the protected scope/fingerprint and retirement metadata without original source content. Exact retention/deletion periods require team policy; until approved, no automatic key reuse or tombstone purge is authorized by this draft. If privacy policy requires complete removal of the deduplication history, document the loss of replay protection and revoke/rotate the relevant intake namespace before accepting old replays again.

### Separation from remote execution

A local accepted task is not proof of remote acceptance. Persist an independent stable remote dispatch correlation/idempotency reference per logical dispatch attempt. Do not pass a user key unchanged across different agents, tenants or operations and assume the agent has the same scope. Verify the adapter's actual scope and retention guarantee; once its retention window ends, its original key may no longer protect against duplicate work.

Timeout after a remote send requires reconciliation or an established still-valid remote idempotency guarantee. An ACCEPTED local record, a new key, a lease timeout, or a retry-delay hint is not evidence that remote work did not start. The current multiplexer HTTP draft accepts an idempotency key but that alone does not demonstrate provider deduplication. P0.7 below defines the proposed remote acceptance/reconciliation agreement; no other service implementation is changed here.

Tests establish deterministic fingerprints, schema constraints and conflict-code mappings, including an independently calculated digest. They do not prove database uniqueness, crash recovery, authorization, tombstone retention, immutable-reference storage or remote duplicate prevention. Those require ledger/worker integration tests.

## Remote acceptance and recovery (P0.7)

This is a proposed agreement with the multiplexer owner, not confirmation that every adapter implements it. `DispatchAttempt` is persisted before sending; `DispatchOutcome` normalizes observations; `conventions/remote-recovery.json` records guarded recovery rules. The current contracts remain draft pending adapter-specific evidence and team review.

### Attempt identity and acceptance

A dispatch attempt binds the owning principal/tenant, task ID/version, selected agent, correlation ID, remote idempotency key, payload fingerprint and start time. Persist these before any network send. The fingerprint must cover the immutable actual agent submission, including execution constraints; the P0.6 client-request digest alone cannot cover fields added during routing. The exact agent submission schema/profile is a shared multiplexer integration decision before execution is implemented.

Verify every response's task/agent/correlation against the persisted attempt and authenticated expected service. Nested RemoteTaskRef/RemoteResult identifiers must match the envelope and attempt. JSON Schema cannot compare those field values or authenticate the sender. Unexpected owners, mismatched identifiers, malformed responses and contradictory observations are quarantined for reconciliation; do not treat them as successful acceptance or conclusive rejection.

`ACCEPTED` means the owning agent has durably accepted the work and supplied a verifiable remote task ID. A multiplexer merely receiving HTTP bytes, returning 2xx, enqueueing ephemeral work, or returning an empty receipt does not establish remote acceptance. Persist the reference before acknowledging the queue item. The coordination state may become RUNNING, meaning remote ownership is confirmed; the agent may still have queued the actual execution.

`TERMINAL` carries a verified RemoteResult and can report immediate completion/failure/cancellation. Persist remote identity and terminal result together; no separate RUNNING notification is required. A generic transport exception, timeout, status outage or missing result is not terminal evidence.

`NOT_ACCEPTED` requires authoritative evidence that this attempt was never accepted and no old request can subsequently be accepted. `failure_class` distinguishes a transient rejection eligible for bounded retry consideration from a permanent rejection that fails locally. Error code alone cannot establish either condition. An authenticated negative response from a durable gate before forwarding, or trustworthy local proof that no request was sent, may qualify; the adapter must document the exact guarantee. A network error whose send stage is uncertain does not qualify.

`UNKNOWN` covers timeouts, disconnects, malformed replies and uncertain acceptance. Preserve the attempt's identity and set UNKNOWN while reconciling. There may already be live remote work. Do not create a fresh task, switch agents, create a new dispatch key, or mark execution failed solely because the request timed out.

### Reconciliation outcomes

- **FOUND:** verify and persist the existing remote reference, then observe its status/result through supported mechanisms. Do not submit another copy. A found receipt can restore RUNNING only if the task is nonterminal; it cannot downgrade a terminal task.
- **ABSENT:** means this particular dispatch was never accepted, not merely that a remote task cannot currently be found. Require authenticated authoritative evidence, matching attempt/start-time coverage, `never_accepted: true`, `acceptance_closed: true`, and an unexpired `valid_until`. Verify the proof against the adapter's guarantees; booleans in a payload are not proof by themselves.
- **UNKNOWN:** preserve uncertainty for temporary outage, eventually consistent empty search, retention gaps, expired evidence or contradictory records. Continue bounded observation or escalate for operator reconciliation.
- **UNSUPPORTED:** the owner lacks a usable lookup interface. Preserve UNKNOWN and require operator evidence or a separately verified idempotency guarantee; never pretend a chat UI is a reconciliation interface.

ABSENT cannot justify retry when prior durable acceptance, a result, or an authenticated callback shows that work started. A deleted/expired remote record after acceptance may mean execution already happened. Quarantine contradictory absence claims. A previously accepted job becomes FAILED only upon positive verified terminal failure or confirmed unrecoverable outcome under an agreed owner/operator procedure; missing status alone is insufficient.

Before using absence to dispatch again, ensure the original request cannot later emerge from a proxy, queue or delayed adapter and execute. Evidence expiry, missing fencing/closure guarantees or a gap in retention invalidates the retry path. Confirm current permissions/approval, unchanged action constraints and remaining retry budget immediately before dispatch. A fresh attempt after conclusive non-acceptance gets its own persisted correlation while retaining the local task; the superseded attempt remains auditable. Retransmitting an unresolved attempt under a valid idempotency guarantee instead retains its original remote key, identity and immutable payload.

### Guarded recovery behavior

| Observation                                | Coordination effect                           | Side-effect policy                                       |
| ------------------------------------------ | --------------------------------------------- | -------------------------------------------------------- |
| Durable remote acceptance                  | RUNNING with remote reference                 | Observe the same job                                     |
| Verified terminal result                   | SUCCEEDED, FAILED or CANCELLED                | Record once; do not resubmit                             |
| Proven non-acceptance, transient           | RETRY_PENDING if authorized and within budget | Bounded retry only after proof verification              |
| Proven non-acceptance, permanent           | FAILED                                        | No automatic retry                                       |
| Ambiguous dispatch                         | UNKNOWN                                       | Reconcile; no new dispatch by default                    |
| Authoritative absence, no prior acceptance | RETRY_PENDING if all guards pass              | Fresh attempt only after closing the old acceptance path |
| Inconclusive/unsupported lookup            | UNKNOWN                                       | Observe or operator review                               |

The convention file is descriptive policy data, not executable permission. Every rule includes its preconditions. Once local state is terminal, stale observations cannot reopen it. Conflicting terminal results require audit/review, not last-write-wins. A duplicate callback/result is idempotent. A valid callback arriving before the submission response may establish acceptance/terminal state through the same attempt identity.

A same-key retransmission while unresolved is allowed only with a documented end-to-end remote deduplication guarantee for the exact principal/tenant/agent/operation and immutable payload. Confirm the guarantee remains valid for the entire possible acceptance window, not just at retry scheduling time. Deduplication in the local queue or a short-lived multiplexer cache is insufficient if a provider call can still execute twice. Unknown guarantees or retention expiry leave the attempt UNKNOWN.

If a retry budget is exhausted after proven non-acceptance, the task can fail locally with that evidence. If observation/reconciliation budget is exhausted while acceptance remains uncertain, keep UNKNOWN and escalate; time spent waiting is not proof of failure. Restart, stale worker lease and HTTP 5xx are not retry authority. Cancellation requests, routine creation and delivery also require their own outcome evidence; task dispatch proofs cannot be reused as blanket guarantees for those operations.

### Terminal failure and handoff evidence

A remote FAILED result means the remote owner positively reports terminal failure for the exact accepted job. It does not guarantee rollback or absence of earlier side effects. A successful task whose channel delivery fails remains SUCCEEDED with a separate delivery-failure record. Cancellation is terminal only when confirmed; an acknowledgement of a cancellation request is not confirmation.

Before enabling an adapter's automated recovery, require demonstration of: durable acceptance receipt; acceptance-then-lost-response lookup; never-accepted response/lookup semantics; closure of delayed in-flight acceptance; actual deduplication scope and lifetime; status/result/callback ordering; and behavior when the remote record expires. Unsupported guarantees stay explicitly unsupported. Only the adapter owner can supply this evidence; no real-agent recovery claim is made by these schema tests.

### Verification boundary

Tests cover complete outcome variants, required attempt identity, terminal-result shape, rejection of contradictory observations, and stronger absence evidence in standalone and generated schemas. They do not verify remote truth, identifier equality, freshness, transaction ordering, safe retries, callback races or crash recovery. The worker and adapter integration harness must test those at implementation time, especially acceptance followed by a lost response and a delayed original request arriving after reconciliation.
