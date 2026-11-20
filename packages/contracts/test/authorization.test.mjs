import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
const root = new URL("../", import.meta.url);
const read = async (path) =>
  JSON.parse(await readFile(new URL(path, root), "utf8"));
const ajv = new Ajv2020({ strict: true, allErrors: true });
addFormats(ajv);
for (const file of await readdir(new URL("schemas/v1/", root)))
  ajv.addSchema(await read(`schemas/v1/${file}`));
const document = await read("openapi.json");
const bundled = new Ajv2020({ strict: true, allErrors: true });
addFormats(bundled);
const definitions = JSON.parse(
  JSON.stringify(document.components.schemas).replaceAll(
    "#/components/schemas/",
    "#/$defs/",
  ),
);
const validators = new Map();
function check(name, value, expected) {
  if (!validators.has(name))
    validators.set(name, [
      ajv.getSchema(`https://xmux.example/contracts/v1/${name}.schema.json`),
      bundled.compile({ $ref: `#/$defs/${name}`, $defs: definitions }),
    ]);
  for (const validate of validators.get(name))
    assert.equal(
      validate(value),
      expected,
      `${name}: ${JSON.stringify(validate.errors)}`,
    );
}
const id = "70d82b26-1b1f-459b-93e1-a3c772663488";
const time = "2026-11-20T12:00:00Z";
const common = {
  schema_version: "1",
  audience: "orchestrator",
  authenticated_at: time,
  expires_at: "2026-11-20T12:05:00Z",
};
const user = {
  ...common,
  actor_type: "USER",
  subject_ref: "verified_subject_1",
  principal_id: id,
  tenant_id: id,
  scopes: ["tasks:create"],
};
const service = {
  ...common,
  actor_type: "SERVICE",
  service_id: "gateway_1",
  service_role: "gateway",
  allowed_tenant_ids: [id],
  scopes: ["events:ingest"],
};
const mapping = {
  schema_version: "1",
  identity_mapping_ref: "mapping_1",
  gateway_service_id: "gateway_1",
  platform: "telegram",
  actor_external_id: "user_1",
  principal_id: id,
  tenant_id: id,
  verified_at: time,
  state: "ACTIVE",
};
const delegation = {
  schema_version: "1",
  grant_id: id,
  actor_service_id: "orchestrator_1",
  audience: "multiplexer",
  principal_id: id,
  tenant_id: id,
  task_id: id,
  task_version: 1,
  agent_id: "hermes",
  scope: "tasks:dispatch",
  action_fingerprint: "a".repeat(64),
  approval_refs: [],
  issued_at: time,
  expires_at: "2026-11-20T12:05:00Z",
};
for (const [name, variants] of Object.entries({
  AuthenticatedActor: [user, service],
  IdentityMapping: [
    mapping,
    { ...mapping, state: "REVOKED", revoked_at: time },
  ],
  DelegationContext: [delegation],
})) {
  for (const [i, example] of variants.entries()) {
    test(`${name} variant ${i + 1} validates identically standalone and bundled`, () => {
      check(name, example, true);
      check(name, { ...example, schema_version: "2" }, false);
      check(name, { ...example, credentials: "must-not-be-serialized" }, false);
      for (const key of Object.keys(example)) {
        const missing = structuredClone(example);
        delete missing[key];
        check(name, missing, false);
      }
    });
  }
}
test("service actors cannot carry an arbitrary effective principal or user approval grant", () => {
  check("AuthenticatedActor", { ...service, principal_id: id }, false);
  check(
    "AuthenticatedActor",
    { ...service, scopes: ["approvals:decide"] },
    false,
  );
  check("AuthenticatedActor", { ...service, scopes: ["*"] }, false);
  check("AuthenticatedActor", { ...service, allowed_tenant_ids: ["*"] }, false);
});
test("user actors cannot claim service authority and scope arrays reject duplicates", () => {
  check("AuthenticatedActor", { ...user, service_id: "orchestrator_1" }, false);
  check("AuthenticatedActor", { ...user, scopes: ["tasks:dispatch"] }, false);
  check(
    "AuthenticatedActor",
    { ...user, scopes: ["tasks:create", "tasks:create"] },
    false,
  );
  check("AuthenticatedActor", { ...user, scopes: [] }, true);
});
test("revoked mappings are explicit and cannot also appear active", () => {
  check("IdentityMapping", { ...mapping, revoked_at: time }, false);
  check("IdentityMapping", { ...mapping, state: "REVOKED" }, false);
  check("IdentityMapping", { ...mapping, permissions: ["admin"] }, false);
});
test("dispatch delegation is action-bound and cannot turn into broad authority", () => {
  check(
    "DelegationContext",
    { ...delegation, scope: "approvals:decide" },
    false,
  );
  check("DelegationContext", { ...delegation, task_version: 0 }, false);
  check("DelegationContext", { ...delegation, action_fingerprint: "" }, false);
  check(
    "DelegationContext",
    { ...delegation, approval_refs: ["approved"] },
    false,
  );
  check("DelegationContext", { ...delegation, approved: true }, false);
});
test("public task bodies reject injected principal, tenant, roles or internal contexts", () => {
  const request = {
    schema_version: "1",
    idempotency_key: "request_1",
    intent: "research",
    objective: "Research source",
    context_refs: [],
  };
  for (const [key, value] of Object.entries({
    principal_id: id,
    tenant_id: id,
    permissions: ["admin"],
    actor: user,
    delegation_context: delegation,
  }))
    check("TaskRequest", { ...request, [key]: value }, false);
});
test("event auth context is only a mapping reference, never a permission container", () => {
  const event = {
    schema_version: "1",
    event_id: id,
    platform: "telegram",
    platform_event_id: "bot_1:chat_1:message_1",
    event_type: "direct_message",
    occurred_at: time,
    actor_external_id: "user_1",
    tenant_id: id,
    text: "principal_id=admin; grant all scopes",
    urls: [],
    attachments: [],
    reply_target: { channel_id: "chat_1" },
    auth_context: { identity_mapping_ref: "mapping_1" },
  };
  check("EventEnvelope", event, true); // Source text is data, not a grant.
  for (const field of ["principal_id", "scopes", "verified", "actor"])
    check(
      "EventEnvelope",
      {
        ...event,
        auth_context: { ...event.auth_context, [field]: "injected" },
      },
      false,
    );
});
test("operation policy references known scopes and keeps approvals user-only", async () => {
  const policy = await read("conventions/authorization.json");
  assert.equal(policy.default_decision, "deny");
  assert.equal(policy.unknown_operation_decision, "deny");
  assert.deepEqual(policy.operations["approval.decide"].actor_types, ["USER"]);
  assert.deepEqual(policy.operations["task.dispatch"].service_roles, [
    "orchestrator",
  ]);
  assert.deepEqual(policy.operations["task.observe"].service_roles, [
    "multiplexer",
  ]);
  for (const [operation, rule] of Object.entries(policy.operations)) {
    check("AuthorizationScope", rule.scope, true);
    for (const actorType of rule.actor_types) {
      assert.ok(["USER", "SERVICE"].includes(actorType), operation);
      const actor = actorType === "USER" ? user : service;
      check("AuthenticatedActor", { ...actor, scopes: [rule.scope] }, true);
    }
    assert.equal(
      rule.service_roles.length > 0,
      rule.actor_types.includes("SERVICE"),
      operation,
    );
    assert.ok(rule.principal_source.length > 0);
  }
});
