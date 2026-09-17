// Runs the shared title-filter cases against docs/filters.js: node tests/filters.test.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { isAirOrOceanOnly, isSuitable } from "../docs/filters.js";

const filters = JSON.parse(readFileSync(new URL("../docs/data/filters.json", import.meta.url)));
const cases = JSON.parse(readFileSync(new URL("./filter_cases.json", import.meta.url)));

for (const c of cases) {
  assert.equal(isSuitable(c.title, filters), c.suitable, `suitable: ${c.label}`);
  assert.equal(isAirOrOceanOnly(c.title, filters), c.air_ocean_only, `air/ocean: ${c.label}`);
}
assert.equal(isSuitable("Implementation Consultant", filters, ["implementation"]), true, "extra keywords");
console.log(`JS filter: ${cases.length + 1} cases passed`);
