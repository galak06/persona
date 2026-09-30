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
  jev_group_gate: JevGateMode;
}

/** A missing mode reads as "off", the same as the engine and the API. */
export function jevBaseline(
  saved: Partial<JevGateValues> | null | undefined,
): JevGateValues {
  return {
    jev_post_gate_ig: saved?.jev_post_gate_ig ?? "off",
    jev_post_gate_fb: saved?.jev_post_gate_fb ?? "off",
    jev_group_gate: saved?.jev_group_gate ?? "off",
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
  if (form.jev_group_gate !== baseline.jev_group_gate) {
    out.jev_group_gate = form.jev_group_gate;
  }
  return out;
}

/**
 * The Jev fields for the next save.
 *
 * After a FAILED save the baseline cannot be trusted: the PATCH persists the
 * row before provisioning runs, so a provisioning 502 can leave the DB on the
 * new mode while `baseline` still holds the old one. Reverting the select then
 * diffs as "unchanged", the field is omitted, and the DB silently stays on
 * the mode the user just undid (review R4). So until a save succeeds again,
 * send all three -- the server writes whatever the form says.
 */
export function jevModesToSend(
  form: JevGateValues,
  baseline: JevGateValues,
  baselineTrusted: boolean,
): Partial<JevGateValues> {
  if (baselineTrusted) return changedJevModes(form, baseline);
  return {
    jev_post_gate_ig: form.jev_post_gate_ig,
    jev_post_gate_fb: form.jev_post_gate_fb,
    jev_group_gate: form.jev_group_gate,
  };
}

/** The API's warning when the modes could not be stored (migration pending). */
export function jevModesNotSaved(warnings: readonly string[] | undefined): boolean {
  return (warnings ?? []).some((w) => w.startsWith("Jev gate modes were NOT saved"));
}
