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
const doc = await read("openapi.json");
const conventions = await read("conventions/http.json");
const definitions = JSON.parse(
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
const error = {
  schema_version: "1",
  code: "validation_failed",
  message: "Payload validation failed",
  correlation_id: id,
  retry_after_seconds: null,
  violations: [{ path: "/objective", reason: "required" }],
};

test("closed error vocabulary and HTTP mapping remain in sync", () => {
  assert.deepEqual(
    [...doc.components.schemas.ErrorCode.enum].sort(),
    Object.keys(conventions.errors).sort(),
  );
  for (const code of Object.keys(conventions.errors))
    check("ErrorCode", code, true);
  check("ErrorCode", "invented_provider_error", false);
});
test("validation errors require bounded structured violations only for validation_failed", () => {
  check("ErrorEnvelope", error, true);
  const without = { ...error };
  delete without.violations;
  check("ErrorEnvelope", without, false);
  check("ErrorEnvelope", { ...error, violations: [] }, false);
  check(
    "ErrorEnvelope",
    {
      ...error,
      violations: Array.from({ length: 51 }, (_, i) => ({
        path: `/items/${i}`,
        reason: "type",
      })),
    },
    false,
  );
  check("ErrorEnvelope", { ...error, code: "internal_error" }, false);
  check("ErrorEnvelope", { ...without, code: "internal_error" }, true);
});
test("field errors use JSON Pointer escapes and cannot carry rejected values", () => {
  for (const path of ["", "/objective", "/urls/0", "/a~1b/~0key"])
    check("FieldViolation", { path, reason: "format" }, true);
  for (const path of [
    "objective",
    "#/objective",
    "/broken~2escape",
    "/trailing~",
  ])
    check("FieldViolation", { path, reason: "format" }, false);
  check(
    "FieldViolation",
    { path: "/objective", reason: "type", value: "private source text" },
    false,
  );
  check("FieldViolation", { path: "/objective", reason: "whatever" }, false);
});
test("task list limits and filters are typed and bounded without silent coercion", () => {
  for (const query of [
    {},
    { limit: 1 },
    { limit: 100, status: "RUNNING" },
    { cursor: "abcdefghijklmnop.signature" },
  ])
    check("TaskListQuery", query, true);
  for (const query of [
    { limit: 0 },
    { limit: 101 },
    { limit: 1.5 },
    { limit: "20" },
    { status: "completed" },
    { offset: 10 },
    { principal_id: id },
    { limit: [20, 30] },
  ])
    check("TaskListQuery", query, false);
  assert.equal(
    doc.components.schemas.TaskListQuery.properties.limit.default,
    20,
  );
});
test("cursor syntax is bounded and pages use an explicit nullable continuation", () => {
  check(
    "TaskPage",
    { schema_version: "1", items: [], next_cursor: null },
    true,
  );
  check(
    "TaskPage",
    { schema_version: "1", items: [], next_cursor: "abcdefghijklmnop" },
    true,
  );
  check("TaskPage", { schema_version: "1", items: [] }, false);
  for (const cursor of [
    "",
    "short",
    "has spaces in cursor",
    "abcdefghijklmnop\r\nInjected",
    "a".repeat(2049),
  ])
    check("TaskListQuery", { cursor }, false);
  check(
    "TaskPage",
    { schema_version: "2", items: [], next_cursor: null },
    false,
  );
  check(
    "TaskPage",
    { schema_version: "1", items: [{}], next_cursor: null },
    false,
  );
});
test("page contents use TaskStatus and are capped at 100 entries", () => {
  const item = {
    schema_version: "1",
    task_id: id,
    status: "QUEUED",
    assigned_agent: null,
    remote_task_id: null,
    created_at: "2026-11-20T10:00:00Z",
    updated_at: "2026-11-20T10:00:00Z",
    error_code: null,
    result_refs: [],
  };
  check(
    "TaskPage",
    { schema_version: "1", items: [item], next_cursor: null },
    true,
  );
  check(
    "TaskPage",
    { schema_version: "1", items: Array(101).fill(item), next_cursor: null },
    false,
  );
});
test("correlation header and pagination parameters share the underlying contracts", () => {
  const params = doc.components.parameters;
  assert.equal(params.CorrelationId.name, "X-Correlation-ID");
  assert.equal(params.CorrelationId.in, "header");
  assert.equal(params.CorrelationId.required, false);
  assert.deepEqual(
    params.CorrelationId.schema,
    doc.components.headers.CorrelationId.schema,
  );
  assert.deepEqual(
    params.PageLimit.schema,
    doc.components.schemas.TaskListQuery.properties.limit,
  );
  const uuid = ajv.compile({
    $ref: "https://xmux.example/contracts/v1/Common.schema.json#/$defs/Uuid",
  });
  for (const value of [id, id.toUpperCase()]) assert.equal(uuid(value), true);
  for (const value of [
    "task_123",
    "urn:uuid:" + id,
    id + ", " + id,
    id + "\r\nInjected: true",
  ])
    assert.equal(uuid(value), false);
});
test("task status error codes use the same closed vocabulary", () => {
  const validate = ajv.getSchema(
    "https://xmux.example/contracts/v1/ErrorCode.schema.json",
  );
  assert.equal(validate("remote_failure"), true);
  assert.equal(validate("raw_provider_exception"), false);
  assert.deepEqual(
    doc.components.schemas.TaskStatus.properties.error_code.anyOf,
    [{ $ref: "#/components/schemas/ErrorCode" }, { type: "null" }],
  );
});

test("UTC timestamps reject ambiguous offsets and impossible dates", () => {
  const validate = ajv.compile({
    $ref: "https://xmux.example/contracts/v1/Common.schema.json#/$defs/UtcTimestamp",
  });
  for (const value of ["2026-11-20T10:00:00Z", "2026-11-20T10:00:00.123Z"])
    assert.equal(validate(value), true);
  for (const value of [
    "2026-11-20",
    "2026-11-20T10:00:00",
    "2026-11-20T10:00:00+00:00",
    "2026-02-30T10:00:00Z",
  ])
    assert.equal(validate(value), false);
});
test("every HTTP error component accepts only its mapped codes and includes correlation", () => {
  for (const [name, response] of Object.entries(doc.components.responses)) {
    const status = Number(name.replace("Error", ""));
    assert.ok(response.headers["X-Correlation-ID"]);
    assert.equal(Boolean(response.headers["WWW-Authenticate"]), status === 401);
    const schema = JSON.parse(
      JSON.stringify(response.content["application/json"].schema).replaceAll(
        "#/components/schemas/",
        "#/$defs/",
      ),
    );
    const validate = bundled.compile({ ...schema, $defs: definitions });
    for (const [code, mapping] of Object.entries(conventions.errors)) {
      const candidate = { ...error, code };
      if (code !== "validation_failed") delete candidate.violations;
      assert.equal(
        validate(candidate),
        mapping.http_status === status,
        `${code} mapped incorrectly to ${status}`,
      );
    }
  }
});
