// Contract reference implementation for tests/interoperability, not an API or deduplication store.
import { readFile, readdir } from "node:fs/promises";
import { createHash } from "node:crypto";
import canonicalize from "canonicalize";
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
const root = new URL("../", import.meta.url);
const read = async (path) =>
  JSON.parse(await readFile(new URL(path, root), "utf8"));
const rules = await read("conventions/idempotency.json");
const ajv = new Ajv2020({ strict: true, allErrors: true });
addFormats(ajv);
for (const file of await readdir(new URL("schemas/v1/", root)))
  ajv.addSchema(await read(`schemas/v1/${file}`));
const identityValidator = ajv.compile({
  type: "object",
  additionalProperties: false,
  required: ["tenant_id", "principal_id"],
  properties: Object.fromEntries(
    ["tenant_id", "principal_id"].map((key) => [
      key,
      {
        $ref: "https://xmux.example/contracts/v1/Common.schema.json#/$defs/Uuid",
      },
    ]),
  ),
});

// Reject non-JSON values rather than silently omitting them during serialization.
function assertJson(value, depth = 0) {
  if (depth > 16) throw new Error("invalid_json_value");
  if (typeof value === "string") {
    if (!value.isWellFormed()) throw new Error("invalid_unicode");
    return;
  }
  if (value === null || typeof value === "boolean") return;
  if (typeof value === "number" && Number.isFinite(value)) return;
  if (typeof value !== "object") throw new Error("invalid_json_value");
  if (Array.isArray(value)) {
    if (Object.keys(value).length !== value.length)
      throw new Error("invalid_json_value");
    for (let i = 0; i < value.length; i++) {
      if (!Object.hasOwn(value, i)) throw new Error("invalid_json_value");
      assertJson(value[i], depth + 1);
    }
  } else {
    if (
      Object.getPrototypeOf(value) !== Object.prototype &&
      Object.getPrototypeOf(value) !== null
    )
      throw new Error("invalid_json_value");
    if (Object.getOwnPropertySymbols(value).length)
      throw new Error("invalid_json_value");
    for (const [key, descriptor] of Object.entries(
      Object.getOwnPropertyDescriptors(value),
    )) {
      if (!Object.hasOwn(descriptor, "value") || !descriptor.enumerable)
        throw new Error("invalid_json_value");
      assertJson(key, depth + 1);
      assertJson(descriptor.value, depth + 1);
    }
  }
}

export function fingerprintRequest(operation, request, trustedIdentity) {
  if (!Object.hasOwn(rules.operations, operation))
    throw new Error("unsupported_operation");
  assertJson(request);
  assertJson(trustedIdentity);
  if (!identityValidator(trustedIdentity))
    throw new Error("invalid_identity_shape");
  const rule = rules.operations[operation];
  const validate = ajv.getSchema(
    `https://xmux.example/contracts/v1/${rule.schema}.schema.json`,
  );
  if (!validate(request)) throw new Error("invalid_request_shape");
  const payload = structuredClone(request);
  for (const field of rule.exclude_fields) delete payload[field];
  for (const field of rule.uuid_fields)
    if (Object.hasOwn(payload, field))
      payload[field] = payload[field].toLowerCase();
  const tenant_id = trustedIdentity.tenant_id.toLowerCase();
  const principal_id = trustedIdentity.principal_id.toLowerCase();
  if (operation === "event.ingest" && payload.tenant_id !== tenant_id)
    throw new Error("identity_mismatch");
  const canonical = canonicalize({
    profile: rules.fingerprint_profile,
    operation,
    tenant_id,
    principal_id,
    payload,
  });
  return {
    profile: rules.fingerprint_profile,
    digest: createHash("sha256").update(canonical, "utf8").digest("hex"),
  };
}
