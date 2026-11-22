import { readFile, readdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const root = new URL("../", import.meta.url);
const schemaDir = new URL("schemas/v1/", root);
const files = (await readdir(schemaDir))
  .filter((file) => file.endsWith(".schema.json"))
  .sort();
const entries = await Promise.all(
  files.map(async (file) => [
    file.replace(".schema.json", ""),
    JSON.parse(await readFile(new URL(file, schemaDir), "utf8")),
  ]),
);
const ids = new Map(entries.map(([name, schema]) => [schema.$id, name]));

// Remove resource IDs when embedding, then resolve references inside this document.
function embed(value) {
  if (Array.isArray(value)) return value.map(embed);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(
    Object.entries(value)
      .filter(([key]) => key !== "$id" && key !== "$schema")
      .map(([key, item]) => {
        if (key !== "$ref") return [key, embed(item)];
        const [id, fragment = ""] = item.split("#");
        if (!ids.has(id))
          throw new Error(`Unresolved schema resource: ${item}`);
        return [key, `#/components/schemas/${ids.get(id)}${fragment}`];
      }),
  );
}

const conventions = JSON.parse(
  await readFile(new URL("conventions/http.json", root), "utf8"),
);
const pointer = (name) => ({ $ref: `#/components/schemas/${name}` });
const commonPointer = (name) => pointer(`Common/$defs/${name}`);
const correlation = {
  description:
    "Single UUID correlation identifier. Echo the validated request value or generate one; it is not authorization or an idempotency key.",
  schema: commonPointer("Uuid"),
};
const parameters = {
  CorrelationId: {
    name: "X-Correlation-ID",
    in: "header",
    required: false,
    ...correlation,
  },
  PageLimit: {
    name: "limit",
    in: "query",
    required: false,
    schema: { type: "integer", minimum: 1, maximum: 100, default: 20 },
  },
  PageCursor: {
    name: "cursor",
    in: "query",
    required: false,
    schema: commonPointer("Cursor"),
  },
  TaskStatusFilter: {
    name: "status",
    in: "query",
    required: false,
    schema: commonPointer("TaskState"),
  },
};
const headers = {
  CorrelationId: correlation,
  RetryAfter: {
    description:
      "Optional integer delay in seconds matching retry_after_seconds when present. Does not authorize retry.",
    schema: { type: "string", pattern: "^(0|[1-9][0-9]*)$" },
  },
  WwwAuthenticate: {
    description:
      "Required authentication challenge on HTTP 401; configured by the authentication layer.",
    schema: { type: "string", minLength: 1 },
  },
};
const statuses = [
  ...new Set(
    Object.values(conventions.errors).map((error) => error.http_status),
  ),
].sort();
const responses = Object.fromEntries(
  statuses.map((status) => {
    const codes = Object.entries(conventions.errors)
      .filter(([, entry]) => entry.http_status === status)
      .map(([code]) => code);
    return [
      `Error${status}`,
      {
        description: `HTTP ${status}: ${codes.join(", ")}. HTTP mapping applies when this operation fails; a successful status lookup may contain a remote error without failing HTTP.`,
        headers: {
          "X-Correlation-ID": { $ref: "#/components/headers/CorrelationId" },
          ...(status === 401
            ? {
                "WWW-Authenticate": {
                  $ref: "#/components/headers/WwwAuthenticate",
                },
              }
            : {}),
          ...([429, 503].includes(status)
            ? { "Retry-After": { $ref: "#/components/headers/RetryAfter" } }
            : {}),
        },
        content: {
          "application/json": {
            schema: {
              allOf: [
                pointer("ErrorEnvelope"),
                {
                  type: "object",
                  properties: { code: { type: "string", enum: codes } },
                },
              ],
            },
          },
        },
      },
    ];
  }),
);

const api = JSON.parse(
  await readFile(new URL("conventions/api.json", root), "utf8"),
);
const fixtureManifest = JSON.parse(
  await readFile(new URL("fixtures/manifest.json", root), "utf8"),
);
const examples = {};
for (const fixture of fixtureManifest.fixtures.filter((item) => item.valid)) {
  examples[fixture.id] = {
    summary: `${fixture.schema}: ${fixture.category}`,
    value: JSON.parse(
      await readFile(new URL(`fixtures/${fixture.path}`, root), "utf8"),
    ),
  };
}
const paths = {};
for (const operation of api.operations) {
  const response = {
    description: "Draft contract response; no deployed service is implied.",
    headers: {
      "X-Correlation-ID": { $ref: "#/components/headers/CorrelationId" },
    },
    content: {
      "application/json": { schema: pointer(operation.response_schema) },
    },
  };
  const operationParameters = [
    { $ref: "#/components/parameters/CorrelationId" },
  ];
  if (operation.path.includes("{id}"))
    operationParameters.push({
      name: "id",
      in: "path",
      required: true,
      schema: commonPointer("Uuid"),
    });
  if (operation.path.includes("{agent_id}"))
    operationParameters.push({
      name: "agent_id",
      in: "path",
      required: true,
      schema: commonPointer("Reference"),
    });
  if (operation.operation_id === "task.list")
    operationParameters.push(
      ...["PageLimit", "PageCursor", "TaskStatusFilter"].map((name) => ({
        $ref: `#/components/parameters/${name}`,
      })),
    );
  const entry = {
    operationId: operation.operation_id,
    summary: `Draft ${operation.operation_id}`,
    "x-service": operation.service,
    "x-required-scope": operation.scope,
    "x-policy-operation": operation.policy_operation,
    description:
      "Proposed contract only. Service authentication, scope, ownership, approval and idempotency enforcement must be implemented separately. Cancellation acceptance is not confirmed cancellation; dispatch HTTP 200 can carry UNKNOWN.",
    servers: [
      {
        url: `https://${operation.service}.example.invalid`,
        description:
          "Documentation placeholder; internal services must not be exposed publicly.",
      },
    ],
    security: [{ VerifiedBearer: [] }],
    parameters: operationParameters,
    responses: {
      ...Object.fromEntries(
        statuses.map((status) => [
          String(status),
          { $ref: `#/components/responses/Error${status}` },
        ]),
      ),
      [String(operation.success_status)]: response,
    },
  };
  if (
    operation.success_status === 202 &&
    operation.operation_id !== "task.cancel"
  )
    entry.responses["200"] = {
      ...response,
      description:
        "Authorized replay of the original resource; no new side effect.",
    };
  if (operation.request_schema)
    entry.requestBody = {
      required: true,
      content: {
        "application/json": { schema: pointer(operation.request_schema) },
      },
    };
  paths[operation.path] ??= {};
  paths[operation.path][operation.method] = entry;
}
const packageInfo = JSON.parse(
  await readFile(new URL("package.json", root), "utf8"),
);

const output =
  JSON.stringify(
    {
      openapi: "3.1.1",
      info: {
        title: "XMUX shared contracts (draft)",
        version: packageInfo.version,
        description:
          "P0 draft schemas, endpoint proposals and shared examples. Bearer verification and scope enforcement require service implementation; this document describes no deployed service.",
      },
      jsonSchemaDialect: "https://json-schema.org/draft/2020-12/schema",
      paths,
      components: {
        examples,
        securitySchemes: {
          VerifiedBearer: {
            type: "http",
            scheme: "bearer",
            description:
              "Credential validation, issuer, audience and scope configuration are pending deployment decisions. No token format is implied.",
          },
        },
        parameters,
        headers,
        responses,
        schemas: Object.fromEntries(
          entries.map(([name, schema]) => [name, embed(schema)]),
        ),
      },
    },
    null,
    2,
  ) + "\n";
const destination = new URL("openapi.json", root);
if (process.argv.includes("--check")) {
  if ((await readFile(destination, "utf8")) !== output)
    throw new Error("OpenAPI is stale; run npm run generate.");
  console.log("Generated OpenAPI matches source schemas.");
} else {
  await writeFile(destination, output);
  console.log(`Generated ${fileURLToPath(destination)}`);
}
