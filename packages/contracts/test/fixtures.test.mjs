import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { fingerprintRequest } from "../scripts/reference-fingerprint.mjs";
import { playScenario } from "../mock/fixture-stub.mjs";
const root = new URL("../", import.meta.url);
const read = async (path) =>
  JSON.parse(await readFile(new URL(path, root), "utf8"));
const manifest = await read("fixtures/manifest.json");
const schemaFiles = await readdir(new URL("schemas/v1/", root));
const ajv = new Ajv2020({ strict: true, allErrors: true });
addFormats(ajv);
for (const file of schemaFiles) ajv.addSchema(await read(`schemas/v1/${file}`));
const data = new Map(
  await Promise.all(
    manifest.fixtures.map(async (entry) => [
      entry.id,
      await read(`fixtures/${entry.path}`),
    ]),
  ),
);
const entries = new Map(manifest.fixtures.map((entry) => [entry.id, entry]));
const document = await read("openapi.json");
const defs = JSON.parse(
  JSON.stringify(document.components.schemas).replaceAll(
    "#/components/schemas/",
    "#/$defs/",
  ),
);
const bundled = new Ajv2020({ strict: true, allErrors: true });
addFormats(bundled);
const compiled = new Map();
for (const entry of manifest.fixtures) {
  test(`fixture ${entry.id}: expected schema outcome standalone and bundled`, () => {
    if (!compiled.has(entry.schema))
      compiled.set(entry.schema, [
        ajv.getSchema(
          `https://xmux.example/contracts/v1/${entry.schema}.schema.json`,
        ),
        bundled.compile({ $ref: `#/$defs/${entry.schema}`, $defs: defs }),
      ]);
    for (const validate of compiled.get(entry.schema)) {
      assert.ok(validate, entry.schema);
      assert.equal(
        validate(data.get(entry.id)),
        entry.valid,
        JSON.stringify(validate.errors),
      );
      if (entry.expected_validation_keyword)
        assert.ok(
          validate.errors.some(
            (error) => error.keyword === entry.expected_validation_keyword,
          ),
        );
    }
  });
}
test("manifest covers every root schema and required scenario category without orphan files", async () => {
  assert.equal(entries.size, manifest.fixtures.length);
  assert.equal(
    new Set(manifest.fixtures.map((entry) => entry.path)).size,
    manifest.fixtures.length,
  );
  const covered = new Set(
    manifest.fixtures
      .filter((entry) => entry.valid)
      .map((entry) => entry.schema),
  );
  for (const file of schemaFiles)
    if (file !== "Common.schema.json")
      assert.ok(covered.has(file.replace(".schema.json", "")), file);
  const actualFiles = (await readdir(new URL("fixtures/v1/", root)))
    .map((file) => `v1/${file}`)
    .sort();
  assert.deepEqual(
    actualFiles,
    manifest.fixtures.map((entry) => entry.path).sort(),
  );
  const categories = new Set(manifest.scenarios.map((s) => s.category));
  for (const category of [
    "valid",
    "malformed",
    "duplicate",
    "unauthorized",
    "unsupported",
    "ambiguous",
    "completed",
  ])
    assert.ok(categories.has(category));
});
for (const scenario of manifest.scenarios) {
  test(`scenario ${scenario.id}: executable offline response matches API contract`, async () => {
    assert.ok(entries.has(scenario.request_fixture));
    assert.ok(entries.get(scenario.response_fixture)?.valid);
    const replay = await playScenario(scenario.id);
    assert.equal(replay.mock, true);
    assert.equal(replay.status, scenario.http_status);
    assert.deepEqual(replay.body, data.get(scenario.response_fixture));
    assert.equal(
      scenario.behavior_verified_by,
      "future_service_integration_tests",
    );
    const operation = Object.values(document.paths)
      .flatMap((path) => Object.values(path))
      .find((op) => op.operationId === scenario.operation);
    assert.ok(operation, scenario.operation);
    let response = operation.responses[String(replay.status)];
    assert.ok(response, `${scenario.operation}: missing HTTP ${replay.status}`);
    if (response.$ref)
      response = document.components.responses[response.$ref.split("/").at(-1)];
    const schema = JSON.parse(
      JSON.stringify(response.content["application/json"].schema).replaceAll(
        "#/components/schemas/",
        "#/$defs/",
      ),
    );
    const validate = bundled.compile({ ...schema, $defs: defs });
    assert.equal(validate(replay.body), true, JSON.stringify(validate.errors));
  });
}
test("duplicate and changed-input fixture pairs match declared fingerprint behavior", () => {
  for (const pair of manifest.fingerprint_cases) {
    const left = fingerprintRequest(
      pair.operation,
      data.get(pair.left),
      pair.identity,
    );
    const right = fingerprintRequest(
      pair.operation,
      data.get(pair.right),
      pair.identity,
    );
    assert.equal(left.digest === right.digest, pair.equal);
  }
});
test("negative policy scenarios use meaningful setup rather than schema invalidity", () => {
  const other = data.get("other-user"),
    owner = data.get("actor");
  assert.equal(other.tenant_id, owner.tenant_id);
  assert.notEqual(other.principal_id, owner.principal_id);
  assert.equal(data.get("unsupported-schedule").intent, "schedule");
  assert.equal(data.get("capabilities").supports_schedule, false);
  assert.equal(data.get("unknown-dispatch").outcome, "UNKNOWN");
  assert.equal(data.get("task-status").status, "SUCCEEDED");
  assert.deepEqual(
    data.get("task-status").result_refs,
    data.get("remote-result").result_refs,
  );
});
test("endpoint proposals bind service ownership, authorization policy and request/response schemas", async () => {
  const api = await read("conventions/api.json");
  const policy = await read("conventions/authorization.json");
  assert.equal(
    new Set(api.operations.map((op) => op.operation_id)).size,
    api.operations.length,
  );
  for (const op of api.operations) {
    const generated = document.paths[op.path][op.method];
    assert.equal(generated["x-service"], op.service);
    assert.equal(policy.operations[op.policy_operation].scope, op.scope);
    assert.deepEqual(generated.security, [{ VerifiedBearer: [] }]);
    if (op.request_schema)
      assert.ok(document.components.schemas[op.request_schema]);
    assert.ok(document.components.schemas[op.response_schema]);
  }
});
test("offline stub refuses unknown scenarios", async () => {
  await assert.rejects(
    playScenario("not-a-scenario"),
    /unknown_fixture_scenario/,
  );
});
