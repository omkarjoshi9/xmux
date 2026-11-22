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
const definitions = JSON.parse(
  JSON.stringify(document.components.schemas).replaceAll(
    "#/components/schemas/",
    "#/$defs/",
  ),
);
const bundle = new Ajv2020({ strict: true, allErrors: true });
addFormats(bundle);
const validators = new Map();
function check(name, value, expected) {
  if (!validators.has(name))
    validators.set(name, [
      ajv.getSchema(`https://xmux.example/contracts/v1/${name}.schema.json`),
      bundle.compile({ $ref: `#/$defs/${name}`, $defs: definitions }),
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
  code: "remote_failure",
  message: "Remote operation failed",
  correlation_id: id,
  retry_after_seconds: null,
};
const attempt = {
  schema_version: "1",
  task_id: id,
  task_version: 1,
  principal_id: id,
  tenant_id: id,
  agent_id: "hermes",
  correlation_id: id,
  remote_idempotency_key: "dispatch_key_1",
  request_fingerprint: {
    profile: "xmux-jcs-sha256-v1",
    digest: "a".repeat(64),
  },
  started_at: time,
};
const base = {
  schema_version: "1",
  task_id: id,
  agent_id: "hermes",
  correlation_id: id,
  observed_at: time,
};
const evidence = {
  evidence_ref: "audit_1",
  never_accepted: true,
  acceptance_closed: true,
};
const terminal = {
  schema_version: "1",
  remote_ref: remote,
  finished_at: time,
  status: "FAILED",
  error,
};
const outcomes = [
  { ...base, outcome: "ACCEPTED", remote_ref: remote },
  { ...base, outcome: "TERMINAL", result: terminal },
  {
    ...base,
    outcome: "NOT_ACCEPTED",
    failure_class: "TRANSIENT",
    error,
    evidence,
  },
  {
    ...base,
    outcome: "NOT_ACCEPTED",
    failure_class: "PERMANENT",
    error,
    evidence,
  },
  { ...base, outcome: "UNKNOWN", error },
];
for (const value of outcomes) {
  test(`dispatch ${value.outcome}/${value.failure_class ?? ""} validates standalone and bundled`, () => {
    check("DispatchOutcome", value, true);
    check("DispatchOutcome", { ...value, schema_version: "2" }, false);
    for (const key of Object.keys(value)) {
      const missing = structuredClone(value);
      delete missing[key];
      check("DispatchOutcome", missing, false);
    }
  });
}
test("dispatch attempt requires durable identity and an immutable fingerprint", () => {
  check("DispatchAttempt", attempt, true);
  for (const key of Object.keys(attempt)) {
    const missing = structuredClone(attempt);
    delete missing[key];
    check("DispatchAttempt", missing, false);
  }
  check("DispatchAttempt", { ...attempt, task_version: 0 }, false);
  check("DispatchAttempt", { ...attempt, remote_idempotency_key: "" }, false);
  check("DispatchAttempt", { ...attempt, safe_to_retry: true }, false);
});
test("acceptance requires a remote receipt and cannot also claim failure", () => {
  check(
    "DispatchOutcome",
    { ...base, outcome: "ACCEPTED", remote_ref: remote, error },
    false,
  );
  check(
    "DispatchOutcome",
    {
      ...base,
      outcome: "ACCEPTED",
      remote_ref: { ...remote, remote_task_id: null },
    },
    false,
  );
  check(
    "DispatchOutcome",
    { ...base, outcome: "UNKNOWN", error, remote_ref: remote },
    false,
  );
});
test("definitive non-acceptance needs both no prior acceptance and a closed acceptance path", () => {
  for (const flag of ["never_accepted", "acceptance_closed"]) {
    const missing = { ...evidence };
    delete missing[flag];
    check(
      "DispatchOutcome",
      {
        ...base,
        outcome: "NOT_ACCEPTED",
        failure_class: "TRANSIENT",
        error,
        evidence: missing,
      },
      false,
    );
    check(
      "DispatchOutcome",
      {
        ...base,
        outcome: "NOT_ACCEPTED",
        failure_class: "TRANSIENT",
        error,
        evidence: { ...evidence, [flag]: false },
      },
      false,
    );
  }
  check(
    "DispatchOutcome",
    {
      ...base,
      outcome: "NOT_ACCEPTED",
      failure_class: "MAYBE",
      error,
      evidence,
    },
    false,
  );
});
test("terminal dispatch outcome must carry a terminal result rather than an outage or pending status", () => {
  check(
    "DispatchOutcome",
    {
      ...base,
      outcome: "TERMINAL",
      result: { ...terminal, status: "RUNNING" },
    },
    false,
  );
  check("DispatchOutcome", { ...base, outcome: "TERMINAL", error }, false);
  check(
    "DispatchOutcome",
    {
      ...base,
      outcome: "TERMINAL",
      result: {
        schema_version: "1",
        remote_ref: remote,
        finished_at: time,
        status: "SUCCEEDED",
        result_refs: ["result_1"],
      },
    },
    true,
  );
});
test("absence evidence requires expiry, historical coverage and closed acceptance", () => {
  const lookup = {
    schema_version: "1",
    task_id: id,
    agent_id: "hermes",
    correlation_id: id,
    checked_at: time,
    outcome: "ABSENT",
    evidence: {
      ...evidence,
      authoritative: true,
      covers_dispatch_started_at: time,
      valid_until: "2026-11-20T12:05:00Z",
    },
  };
  check("ReconciliationOutcome", lookup, true);
  for (const field of Object.keys(lookup.evidence)) {
    const missing = structuredClone(lookup);
    delete missing.evidence[field];
    check("ReconciliationOutcome", missing, false);
  }
  for (const flag of ["never_accepted", "acceptance_closed", "authoritative"])
    check(
      "ReconciliationOutcome",
      { ...lookup, evidence: { ...lookup.evidence, [flag]: false } },
      false,
    );
});
