import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";

const root = new URL("../", import.meta.url);
const dir = new URL("schemas/v1/", root);
const ajv = new Ajv2020({ strict: true, allErrors: true });
addFormats(ajv);
for (const file of await readdir(dir))
  ajv.addSchema(JSON.parse(await readFile(new URL(file, dir), "utf8")));
const doc = JSON.parse(await readFile(new URL("openapi.json", root), "utf8"));
const bundled = new Ajv2020({ strict: true, allErrors: true });
addFormats(bundled);
const definitions = JSON.parse(
  JSON.stringify(doc.components.schemas).replaceAll(
    "#/components/schemas/",
    "#/$defs/",
  ),
);
const names = [
  "ErrorEnvelope",
  "AgentCapabilities",
  "RemoteResult",
  "TaskCallback",
  "CancellationReceipt",
  "ReconciliationOutcome",
];
const validators = Object.fromEntries(
  names.map((name) => [
    name,
    [
      ajv.getSchema(`https://xmux.example/contracts/v1/${name}.schema.json`),
      bundled.compile({
        $schema: "https://json-schema.org/draft/2020-12/schema",
        $defs: definitions,
        $ref: `#/$defs/${name}`,
      }),
    ],
  ]),
);
function check(name, data, expected) {
  for (const validate of validators[name])
    assert.equal(
      validate(data),
      expected,
      `${name}: ${JSON.stringify(validate.errors)}`,
    );
}
const id = "70d82b26-1b1f-459b-93e1-a3c772663488";
const time = "2026-11-20T12:00:00Z";
const remote = {
  schema_version: "1",
  task_id: id,
  agent_id: "hermes",
  remote_task_id: "job_1",
  correlation_id: id,
  accepted_at: time,
};
const error = {
  schema_version: "1",
  code: "remote_unavailable",
  message: "Remote status is unavailable",
  correlation_id: id,
  retry_after_seconds: null,
};
const capabilities = {
  schema_version: "1",
  agent_id: "hermes",
  operations: ["research"],
  supports_status: true,
  supports_cancel: false,
  supports_schedule: false,
  supports_memory_lookup: false,
  supports_callbacks: false,
  supports_correlation_lookup: false,
  dispatch_idempotency: { supported: false },
};
const terminal = { schema_version: "1", remote_ref: remote, finished_at: time };
const success = { ...terminal, status: "SUCCEEDED", result_refs: ["result_1"] };
const callback = {
  schema_version: "1",
  callback_id: id,
  emitted_at: time,
  sequence: null,
};
const cancellation = {
  schema_version: "1",
  cancellation_id: id,
  remote_ref: remote,
  observed_at: time,
};
const reconciliation = {
  schema_version: "1",
  task_id: id,
  agent_id: "hermes",
  correlation_id: id,
  checked_at: time,
};
const cases = {
  ErrorEnvelope: [error, { ...error, retry_after_seconds: 30 }],
  AgentCapabilities: [
    capabilities,
    {
      ...capabilities,
      dispatch_idempotency: {
        supported: true,
        scope: "principal_agent_operation",
        retention_seconds: 3600,
      },
    },
  ],
  RemoteResult: [
    success,
    { ...terminal, status: "FAILED", error },
    { ...terminal, status: "CANCELLED" },
  ],
  TaskCallback: [
    { ...callback, kind: "status", remote_ref: remote, status: "RUNNING" },
    {
      ...callback,
      kind: "status",
      remote_ref: remote,
      status: "UNKNOWN",
      sequence: 2,
    },
    { ...callback, kind: "result", result: success },
    {
      ...callback,
      kind: "result",
      result: { ...terminal, status: "FAILED", error },
    },
  ],
  CancellationReceipt: [
    { ...cancellation, outcome: "REQUESTED" },
    { ...cancellation, outcome: "CONFIRMED", confirmed_at: time },
    { ...cancellation, outcome: "UNSUPPORTED", error },
    { ...cancellation, outcome: "UNKNOWN", error },
    {
      ...cancellation,
      outcome: "ALREADY_TERMINAL",
      terminal_status: "SUCCEEDED",
    },
  ],
  ReconciliationOutcome: [
    { ...reconciliation, outcome: "FOUND", remote_ref: remote },
    {
      ...reconciliation,
      outcome: "ABSENT",
      evidence: {
        authoritative: true,
        evidence_ref: "lookup_receipt_1",
        covers_dispatch_started_at: time,
      },
    },
    { ...reconciliation, outcome: "UNKNOWN", error },
    { ...reconciliation, outcome: "UNSUPPORTED", error },
  ],
};
for (const [name, variants] of Object.entries(cases)) {
  for (const [index, value] of variants.entries()) {
    test(`${name} variant ${index + 1}: same valid/invalid behavior standalone and bundled`, () => {
      check(name, value, true);
      check(name, { ...value, schema_version: "2" }, false);
      check(name, { ...value, extra: "not-declared" }, false);
      for (const field of Object.keys(value)) {
        const missing = structuredClone(value);
        delete missing[field];
        check(name, missing, false);
      }
    });
  }
}
test("capabilities never silently default to support and idempotency guarantees need bounded scope", () => {
  check(
    "AgentCapabilities",
    { ...capabilities, supports_status: "true" },
    false,
  );
  check(
    "AgentCapabilities",
    { ...capabilities, operations: ["research", "research"] },
    false,
  );
  for (const dispatch_idempotency of [
    { supported: true },
    { supported: true, scope: "global", retention_seconds: 3600 },
    {
      supported: true,
      scope: "principal_agent_operation",
      retention_seconds: 0,
    },
    { supported: false, retention_seconds: 3600 },
  ])
    check(
      "AgentCapabilities",
      { ...capabilities, dispatch_idempotency },
      false,
    );
});
test("terminal results cannot mix success, failure, and pending observations", () => {
  check("RemoteResult", { ...success, error }, false);
  check("RemoteResult", { ...success, result_refs: [] }, false);
  check("RemoteResult", { ...terminal, status: "RUNNING" }, false);
  check(
    "RemoteResult",
    { ...success, remote_ref: { ...remote, remote_task_id: null } },
    false,
  );
});
test("callback type prevents premature terminal status and rejects inline credential fields", () => {
  check(
    "TaskCallback",
    { ...callback, kind: "status", remote_ref: remote, status: "SUCCEEDED" },
    false,
  );
  check(
    "TaskCallback",
    { ...callback, kind: "result", result: success, signature: "secret" },
    false,
  );
  check(
    "TaskCallback",
    { ...callback, kind: "result", result: success, sequence: -1 },
    false,
  );
  check(
    "TaskCallback",
    { ...callback, kind: "result", result: success, callback_id: "not-a-uuid" },
    false,
  );
});
test("requested or unknown cancellation cannot include confirmation evidence", () => {
  check(
    "CancellationReceipt",
    { ...cancellation, outcome: "REQUESTED", confirmed_at: time },
    false,
  );
  check(
    "CancellationReceipt",
    { ...cancellation, outcome: "UNKNOWN", error, confirmed_at: time },
    false,
  );
  check(
    "CancellationReceipt",
    {
      ...cancellation,
      outcome: "ALREADY_TERMINAL",
      terminal_status: "RUNNING",
    },
    false,
  );
  check(
    "CancellationReceipt",
    { ...cancellation, outcome: "CONFIRMED", confirmed_at: "yesterday" },
    false,
  );
});
test("reconciliation cannot claim absence without authoritative evidence or attach invented retry authority", () => {
  check(
    "ReconciliationOutcome",
    { ...reconciliation, outcome: "ABSENT" },
    false,
  );
  check(
    "ReconciliationOutcome",
    {
      ...reconciliation,
      outcome: "ABSENT",
      evidence: {
        authoritative: false,
        evidence_ref: "lookup_1",
        covers_dispatch_started_at: time,
      },
    },
    false,
  );
  check(
    "ReconciliationOutcome",
    { ...reconciliation, outcome: "UNKNOWN", error, remote_ref: remote },
    false,
  );
  check(
    "ReconciliationOutcome",
    {
      ...reconciliation,
      outcome: "FOUND",
      remote_ref: remote,
      safe_to_retry: true,
    },
    false,
  );
});
test("errors are bounded without raw exception bags or implicit retry permission", () => {
  check("ErrorEnvelope", { ...error, message: "x".repeat(2049) }, false);
  check("ErrorEnvelope", { ...error, stack: "raw exception" }, false);
  check("ErrorEnvelope", { ...error, retryable: true }, false);
  check("ErrorEnvelope", { ...error, retry_after_seconds: -1 }, false);
  check("ErrorEnvelope", { ...error, code: "Bad Request" }, false);
});
