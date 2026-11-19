# XMUX shared contracts — draft v1

P0.2 provides seven language-neutral wire schemas and a generated OpenAPI component catalog. These are executable **proposals pending gateway, multiplexer and control-plane agreement**, not stable or deployed APIs.

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

The OpenAPI document intentionally has empty `paths`: this task defines reusable schemas, not service operations, transport authentication, status codes, response receipts or errors. Those contracts require the subsequent P0 tasks. No dispatch or delivery endpoint is implemented here.

## Draft conventions

- `schema_version` is the string `"1"`; schema/package revision is independently `0.1.0-draft.1`. No compatibility promise is made until team approval.
- Objects reject undeclared fields, including nested auth/reply structures. New fields require coordinated producer/consumer updates; do not assume additive changes are automatically compatible.
- XMUX IDs are UUIDs; remote/platform references are opaque nonempty strings. UTC timestamps use an explicit `Z` suffix.
- Task states are uppercase, including approval, retry and unknown states. State legality, timestamp ordering and remote-reference requirements for particular transitions are enforced by later lifecycle services, not this structural schema.
- Direct tasks may omit `source_event_id` and `reply_target_ref`; missing optional fields must be omitted rather than sent as null. Status owner/error fields use explicit null when unavailable.
- `ApprovalRequest` is a decision submission, not an approval-record creation request. The server resolves the approver from authentication and verifies the stored action fingerprint, ownership, expiry and policy.
- `RoutineRef` represents an already confirmed remote schedule. `UNKNOWN` distinguishes an unreachable owner from a confirmed `REMOVED` schedule. Actual schedule status mapping must be agreed with adapters.
- `DeliveryRequest.artifacts` and task results carry authorized opaque references. Their resolution/formatting and final delivery acknowledgement belong to later contracts.
- Input limits (64 KiB text, 16 KiB objective, up to 100 references/attachments, and per-field lengths in schemas) are provisional review choices; service request-byte limits are separate.

## Trust boundaries and pending agreement

Schema validity does not authenticate a request, validate a permission, make a URL safe to fetch, or prove content access. Source text remains data. The receiving service must authenticate the gateway, resolve `identity_mapping_ref`, verify tenant membership and check ownership of every referenced object. Schema rejection of an injected permission field is only structural validation, not a security integration test.

Before these drafts are frozen, confirm identity mapping provision/lookup, tenant scope, platform event namespacing, remote dispatch idempotency and reconciliation guarantees, delivery receipts, approval fingerprint canonicalization, routine state mapping, and optional agent capabilities. Authenticated clients may not choose arbitrary principals or approvers in task/approval bodies.

The event wire field is `platform_event_id`; the gateway must namespace it across bot accounts/chats when necessary. Event deduplication, payload fingerprint conflicts, user isolation, remote execution safety and content retention remain service responsibilities and later P0 contracts.

Local workshop notes and implementation plans stay in ignored `plan/`; they are not dependencies of this package. Broader error/capability/callback schemas and shared cross-team fixture scenarios are intentionally reserved for the next P0 tasks.
