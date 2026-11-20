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

const output =
  JSON.stringify(
    {
      openapi: "3.1.1",
      info: {
        title: "XMUX shared contracts (draft)",
        version: "0.1.0-draft.6",
        description:
          "P0.2–P0.7 draft schema and coordination convention catalog. Empty paths are intentional: service operations and authentication bindings remain future work. This document describes no deployed service.",
      },
      jsonSchemaDialect: "https://json-schema.org/draft/2020-12/schema",
      paths: {},
      components: {
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
