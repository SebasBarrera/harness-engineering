import assert from "node:assert/strict";
import test from "node:test";
import { resolveConfig } from "../src/resolve-config.js";

test("repository overrides defaults", () => {
  assert.deepEqual(resolveConfig({ mode: "safe" }, { mode: "strict" }, {}), { mode: "strict" });
});
