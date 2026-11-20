import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { fingerprintRequest } from "../scripts/reference-fingerprint.mjs";
const id = "70d82b26-1b1f-459b-93e1-a3c772663488";
const another = "39b5ee0b-6ecf-4ef8-8953-a34a8272ec90";
const identity = { tenant_id: id, principal_id: id };
const request = {
  schema_version: "1",
  idempotency_key: "request_1",
  intent: "research",
  objective: "Research this claim",
  context_refs: ["content_1", "content_2"],
};
const fingerprint = fingerprintRequest("task.create", request, identity);
const time = "2026-11-20T12:00:00Z";
const root = new URL("../", import.meta.url);
const read = async (path) =>
  JSON.parse(await readFile(new URL(path, root), "utf8"));
const ajv = new Ajv2020({ strict: true, allErrors: true });
addFormats(ajv);
for (const file of await readdir(new URL("schemas/v1/", root)))
  ajv.addSchema(await read(`schemas/v1/${file}`));
const doc = await read("openapi.json");
const defs = JSON.parse(
  JSON.stringify(doc.components.schemas).replaceAll(
    "#/components/schemas/",
    "#/$defs/",
  ),
);
const bundled = new Ajv2020({ strict: true, allErrors: true });
addFormats(bundled);
const validators = new Map();
function check(name, value, expected) {
  if (!validators.has(name))
    validators.set(name, [
      ajv.getSchema(`https://xmux.example/contracts/v1/${name}.schema.json`),
      bundled.compile({ $ref: `#/$defs/${name}`, $defs: defs }),
    ]);
  for (const validate of validators.get(name))
    assert.equal(
      validate(value),
      expected,
      `${name}: ${JSON.stringify(validate.errors)}`,
    );
}
test("task fingerprint matches an independently calculated canonical digest", () => {
  assert.deepEqual(fingerprint, {
    profile: "xmux-jcs-sha256-v1",
    digest: "9712ff909d52e552521f2b9f9775f1799ba69dc0ae922eaba1f6af4aa99f2053",
  });
});
test("object ordering and key value do not change the payload fingerprint", () => {
  const reversed = Object.fromEntries(Object.entries(request).reverse());
  assert.deepEqual(
    fingerprintRequest("task.create", reversed, identity),
    fingerprint,
  );
  assert.deepEqual(
    fingerprintRequest(
      "task.create",
      { ...request, idempotency_key: "another-key" },
      identity,
    ),
    fingerprint,
  );
});
test("changed intent, objective, destination, references or array ordering changes the fingerprint", () => {
  for (const change of [
    { intent: "delegate" },
    { objective: "Research a different claim" },
    { requested_agent: "hermes" },
    { context_refs: [...request.context_refs].reverse() },
    { reply_target_ref: "reply_2" },
  ])
    assert.notDeepEqual(
      fingerprintRequest("task.create", { ...request, ...change }, identity),
      fingerprint,
    );
});
test("principal and tenant are fingerprint domain boundaries and UUID case normalizes", () => {
  assert.notDeepEqual(
    fingerprintRequest("task.create", request, {
      ...identity,
      principal_id: another,
    }),
    fingerprint,
  );
  assert.notDeepEqual(
    fingerprintRequest("task.create", request, {
      ...identity,
      tenant_id: another,
    }),
    fingerprint,
  );
  assert.deepEqual(
    fingerprintRequest("task.create", request, {
      principal_id: id.toUpperCase(),
      tenant_id: id.toUpperCase(),
    }),
    fingerprint,
  );
  const original = { ...request, source_event_id: id };
  assert.deepEqual(
    fingerprintRequest("task.create", original, identity),
    fingerprintRequest(
      "task.create",
      { ...original, source_event_id: id.toUpperCase() },
      identity,
    ),
  );
});
test("text is preserved exactly; spaces and Unicode normalization are significant", () => {
  const a = fingerprintRequest(
    "task.create",
    { ...request, objective: "caf\u00e9" },
    identity,
  );
  const b = fingerprintRequest(
    "task.create",
    { ...request, objective: "cafe\u0301" },
    identity,
  );
  assert.notDeepEqual(a, b);
  assert.notDeepEqual(
    fingerprintRequest(
      "task.create",
      { ...request, objective: request.objective + " " },
      identity,
    ),
    fingerprint,
  );
});
test("missing optional fields cannot be replaced by null or undefined", () => {
  for (const requested_agent of [null, undefined])
    assert.throws(() =>
      fingerprintRequest(
        "task.create",
        { ...request, requested_agent },
        identity,
      ),
    );
});
test("non-JSON and malformed Unicode values are rejected instead of silently dropped", () => {
  for (const objective of [
    "\ud800",
    NaN,
    Infinity,
    () => "anything",
    new String("boxed"),
  ])
    assert.throws(() =>
      fingerprintRequest("task.create", { ...request, objective }, identity),
    );
  const sparse = Array(1);
  assert.throws(() =>
    fingerprintRequest(
      "task.create",
      { ...request, context_refs: sparse },
      identity,
    ),
  );
  assert.throws(() =>
    fingerprintRequest(
      "task.create",
      { ...request, arbitrary: true },
      identity,
    ),
  );
  assert.throws(
    () => fingerprintRequest("toString", request, identity),
    /unsupported_operation/,
  );
  assert.throws(
    () =>
      fingerprintRequest("task.create", request, {
        ...identity,
        service_id: "fake",
      }),
    /invalid_identity_shape/,
  );
});
test("event replay ignores generated event UUID and mapping handle but retains source content", () => {
  const event = {
    schema_version: "1",
    event_id: id,
    platform: "telegram",
    platform_event_id: "bot_1:chat_42:msg_7",
    event_type: "direct_message",
    occurred_at: time,
    actor_external_id: "user_1",
    tenant_id: id,
    text: "Research this",
    urls: [],
    attachments: [],
    reply_target: { channel_id: "chat_42" },
    auth_context: { identity_mapping_ref: "mapping_1" },
  };
  const first = fingerprintRequest("event.ingest", event, identity);
  assert.deepEqual(
    fingerprintRequest(
      "event.ingest",
      {
        ...event,
        event_id: another,
        auth_context: { identity_mapping_ref: "mapping_2" },
      },
      identity,
    ),
    first,
  );
  assert.notDeepEqual(
    fingerprintRequest(
      "event.ingest",
      { ...event, text: "Changed request" },
      identity,
    ),
    first,
  );
  assert.notDeepEqual(
    fingerprintRequest(
      "event.ingest",
      { ...event, actor_external_id: "user_2" },
      identity,
    ),
    first,
  );
  assert.throws(
    () =>
      fingerprintRequest(
        "event.ingest",
        { ...event, tenant_id: another },
        identity,
      ),
    /identity_mismatch/,
  );
});
test("approval and delivery fingerprints bind the actual action and reply destination", () => {
  const approval = {
    schema_version: "1",
    approval_id: id,
    task_id: id,
    decision: "approve",
    action_fingerprint: "a".repeat(64),
    idempotency_key: "approval_1",
  };
  assert.notDeepEqual(
    fingerprintRequest("approval.decide", approval, identity),
    fingerprintRequest(
      "approval.decide",
      { ...approval, decision: "deny" },
      identity,
    ),
  );
  const delivery = {
    schema_version: "1",
    task_id: id,
    channel: "telegram",
    reply_target: { channel_id: "chat_1" },
    text: "Done",
    artifacts: [],
    idempotency_key: "delivery_1",
  };
  assert.notDeepEqual(
    fingerprintRequest("delivery.submit", delivery, identity),
    fingerprintRequest(
      "delivery.submit",
      { ...delivery, reply_target: { channel_id: "chat_2" } },
      identity,
    ),
  );
});
const scope = {
  schema_version: "1",
  ...identity,
  operation: "task.create",
  idempotency_key: "request_1",
};
const record = { schema_version: "1", scope, fingerprint, created_at: time };
for (const value of [
  { ...record, state: "RESERVED" },
  { ...record, state: "ACCEPTED", resource_ref: "task_1" },
  { ...record, state: "RETIRED", retired_at: time },
]) {
  test(`idempotency record ${value.state} has the same standalone and bundled constraints`, () => {
    check("IdempotencyRecord", value, true);
    check("IdempotencyRecord", { ...value, schema_version: "2" }, false);
    check("IdempotencyRecord", { ...value, safe_to_retry: true }, false);
    for (const key of Object.keys(value)) {
      const missing = structuredClone(value);
      delete missing[key];
      check("IdempotencyRecord", missing, false);
    }
  });
}
test("scopes, digest format and record states reject ambiguous claims", () => {
  check("IdempotencyScope", scope, true);
  check("IdempotencyScope", { ...scope, operation: "event.ingest" }, false);
  check("IdempotencyScope", { ...scope, idempotency_key: "" }, false);
  check(
    "RequestFingerprint",
    { ...fingerprint, digest: fingerprint.digest.toUpperCase() },
    false,
  );
  check("RequestFingerprint", { ...fingerprint, profile: "unknown" }, false);
  check(
    "IdempotencyRecord",
    { ...record, state: "RESERVED", resource_ref: "task_1" },
    false,
  );
  check(
    "IdempotencyRecord",
    { ...record, state: "RETIRED", retired_at: time, resource_ref: "task_1" },
    false,
  );
});
test("request_in_progress and idempotency_conflict are distinct mapped HTTP 409 failures", async () => {
  const http = await read("conventions/http.json");
  assert.equal(http.errors.request_in_progress.http_status, 409);
  assert.equal(http.errors.idempotency_conflict.http_status, 409);
});
