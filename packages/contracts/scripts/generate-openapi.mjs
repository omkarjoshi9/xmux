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

const output =
  JSON.stringify(
    {
      openapi: "3.1.1",
      info: {
        title: "XMUX shared contracts (draft)",
        version: "0.1.0-draft.2",
        description:
          "P0.2–P0.3 schema catalog only. Pending team agreement. Empty paths are intentional: service operations, authentication bindings and HTTP response mappings are defined in later contract tasks. This document describes no deployed service.",
      },
      jsonSchemaDialect: "https://json-schema.org/draft/2020-12/schema",
      paths: {},
      components: {
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
