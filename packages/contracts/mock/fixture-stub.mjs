// Offline API response stub. No server, network, authorization engine or persistence.
import { readFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
const root = new URL("../fixtures/", import.meta.url);
export async function playScenario(id) {
  const manifest = JSON.parse(
    await readFile(new URL("manifest.json", root), "utf8"),
  );
  const scenario = manifest.scenarios.find((item) => item.id === id);
  if (!scenario) throw new Error("unknown_fixture_scenario");
  const response = manifest.fixtures.find(
    (item) => item.id === scenario.response_fixture,
  );
  const body = JSON.parse(await readFile(new URL(response.path, root), "utf8"));
  return {
    mock: true,
    scenario: id,
    status: scenario.http_status,
    expected_policy: scenario.expected_policy,
    body,
  };
}
if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  try {
    console.log(
      JSON.stringify(
        await playScenario(process.argv[2] ?? "valid-task"),
        null,
        2,
      ),
    );
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
