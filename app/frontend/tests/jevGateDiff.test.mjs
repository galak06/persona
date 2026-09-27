// Unit tests for src/components/jevGateDiff.ts, run by `npm test` (node --test).
//
// The frontend has no test framework, so this test transpiles the one pure
// module with the `typescript` devDependency and imports the result. That
// works on the CI runner's Node 20 without any loader flags. The module has
// type-only imports, so it transpiles standalone.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../src/components/jevGateDiff.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
});
const { changedJevModes, jevBaseline, jevModesNotSaved } = await import(
  `data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`
);

const loaded = { jev_post_gate_ig: "shadow", jev_post_gate_fb: "shadow" };

test("untouched modes are not sent", () => {
  assert.deepEqual(changedJevModes({ ...loaded }, jevBaseline(loaded)), {});
});

test("A -> B -> A: the revert is sent because the baseline is the saved value", () => {
  // Save 1: shadow -> enforce.
  let baseline = jevBaseline(loaded);
  const first = changedJevModes({ ...loaded, jev_post_gate_ig: "enforce" }, baseline);
  assert.deepEqual(first, { jev_post_gate_ig: "enforce" });
  // The PATCH response carries the persisted modes; it becomes the baseline.
  baseline = jevBaseline({ ...loaded, jev_post_gate_ig: "enforce" });
  // Save 2: back to shadow -- must be sent, or the DB stays on enforce.
  const second = changedJevModes({ ...loaded, jev_post_gate_ig: "shadow" }, baseline);
  assert.deepEqual(second, { jev_post_gate_ig: "shadow" });
  // The bug this guards: diffing against the PAGE-LOAD value drops it.
  assert.deepEqual(changedJevModes({ ...loaded }, jevBaseline(loaded)), {});
});

test("missing modes read as off, like the engine and the API", () => {
  assert.deepEqual(jevBaseline(null), { jev_post_gate_ig: "off", jev_post_gate_fb: "off" });
  assert.deepEqual(jevBaseline({}), { jev_post_gate_ig: "off", jev_post_gate_fb: "off" });
  assert.deepEqual(
    changedJevModes({ jev_post_gate_ig: "off", jev_post_gate_fb: "shadow" }, jevBaseline({})),
    { jev_post_gate_fb: "shadow" },
  );
});

test("the migration-pending warning is recognised", () => {
  assert.equal(
    jevModesNotSaved([
      "Jev gate modes were NOT saved: the database migration is pending (apply db/schema.sql).",
    ]),
    true,
  );
  assert.equal(jevModesNotSaved(["some other warning"]), false);
  assert.equal(jevModesNotSaved(undefined), false);
});
