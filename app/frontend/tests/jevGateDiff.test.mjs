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
const { changedJevModes, jevBaseline, jevModesNotSaved, jevModesToSend } = await import(
  `data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`
);

const loaded = { jev_post_gate_ig: "shadow", jev_post_gate_fb: "shadow", jev_group_gate: "shadow" };

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
  const allOff = { jev_post_gate_ig: "off", jev_post_gate_fb: "off", jev_group_gate: "off" };
  assert.deepEqual(jevBaseline(null), allOff);
  assert.deepEqual(jevBaseline({}), allOff);
  assert.deepEqual(
    changedJevModes({ ...allOff, jev_post_gate_fb: "shadow" }, jevBaseline({})),
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

test("the FB group gate is diffed like the post gates", () => {
  let baseline = jevBaseline(loaded);
  assert.deepEqual(changedJevModes({ ...loaded, jev_group_gate: "off" }, baseline), {
    jev_group_gate: "off",
  });
  baseline = jevBaseline({ ...loaded, jev_group_gate: "off" });
  assert.deepEqual(changedJevModes({ ...loaded }, baseline), { jev_group_gate: "shadow" });
  assert.deepEqual(changedJevModes({ ...loaded, jev_group_gate: "off" }, baseline), {});
});

test("R4: after a failed save that persisted anyway, the revert still reaches the DB", () => {
  // Save 1: shadow -> enforce. The PATCH writes the row, provisioning 502s,
  // the page never sees a response, so its baseline stays on shadow.
  const baseline = jevBaseline(loaded);
  const reverted = { ...loaded }; // the user sets IG back to shadow
  // A diff would omit the field (shadow == stale shadow) and leave the DB on enforce.
  assert.deepEqual(changedJevModes(reverted, baseline), {});
  // Untrusted baseline: every mode is sent, so the server writes shadow.
  assert.deepEqual(jevModesToSend(reverted, baseline, false), loaded);
});

test("a trusted baseline still sends only the diff", () => {
  const form = { ...loaded, jev_group_gate: "enforce" };
  assert.deepEqual(jevModesToSend(form, jevBaseline(loaded), true), { jev_group_gate: "enforce" });
});
