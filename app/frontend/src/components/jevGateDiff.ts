import type { JevGateMode } from "../api/brands";

/**
 * Pure save-diff logic for the Jev gate mode selects on Brand Settings.
 *
 * This file has type-only imports on purpose. `tests/jevGateDiff.test.mjs`
 * transpiles it on its own and runs it under `node --test`; the frontend has
 * no other test framework.
 */

export interface JevGateValues {
  jev_post_gate_ig: JevGateMode;
  jev_post_gate_fb: JevGateMode;
}

/** A missing mode reads as "off", the same as the engine and the API. */
export function jevBaseline(
  saved: Partial<JevGateValues> | null | undefined,
): JevGateValues {
  return {
    jev_post_gate_ig: saved?.jev_post_gate_ig ?? "off",
    jev_post_gate_fb: saved?.jev_post_gate_fb ?? "off",
  };
}

/**
 * Returns only the modes that differ from `baseline`, the last PERSISTED
 * values.
 *
 * - The baseline must come from the most recent save response, not from the
 *   page load. Otherwise an A→B→A edit compares the final A with the stale A
 *   and the revert is silently dropped (the DB would stay on B).
 * - A save that did not touch the gate sends neither field.
 */
export function changedJevModes(
  form: JevGateValues,
  baseline: JevGateValues,
): Partial<JevGateValues> {
  const out: Partial<JevGateValues> = {};
  if (form.jev_post_gate_ig !== baseline.jev_post_gate_ig) {
    out.jev_post_gate_ig = form.jev_post_gate_ig;
  }
  if (form.jev_post_gate_fb !== baseline.jev_post_gate_fb) {
    out.jev_post_gate_fb = form.jev_post_gate_fb;
  }
  return out;
}

/** The API's warning when the modes could not be stored (migration pending). */
export function jevModesNotSaved(warnings: readonly string[] | undefined): boolean {
  return (warnings ?? []).some((w) => w.startsWith("Jev gate modes were NOT saved"));
}
