import { Link } from "react-router-dom";
import { JEV_GATE_MODES, type JevGateMode } from "../api/brands";
import type { JevGateValues } from "./jevGateDiff";

/**
 * The two Jev post-gate mode selects on Brand Settings (one per engager).
 * Split out of `pages/BrandSettings.tsx` to keep that page from growing; the
 * save-diff logic lives in `jevGateDiff.ts` (unit-tested).
 */

export type { JevGateValues };

const MODE_LABEL: Record<JevGateMode, string> = {
  off: "Off — not consulted",
  shadow: "Shadow — log decisions only",
  enforce: "Enforce — may skip the drafter",
};

const FIELDS: { key: keyof JevGateValues; label: string }[] = [
  { key: "jev_post_gate_ig", label: "Instagram engager" },
  { key: "jev_post_gate_fb", label: "Facebook engager" },
];

export default function JevGateFields({
  values,
  onChange,
}: {
  values: JevGateValues;
  onChange: (next: JevGateValues) => void;
}): React.JSX.Element {
  return (
    <div className="border-t border-stone-100 pt-4 space-y-2">
      <p className="text-sm font-medium text-slate-700">Jev post gate</p>
      <p className="text-xs text-slate-500">
        A cheap classifier asked before the comment drafter runs. Shadow mode never changes what
        the engager does — compare its calls on the{" "}
        <Link to="/decisions" className="text-amber-700 hover:underline">
          Decisions
        </Link>{" "}
        page before switching to enforce.
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {FIELDS.map((field) => (
          <label key={field.key} className="text-sm">
            <span className="block mb-1 font-medium text-slate-700">{field.label}</span>
            <select
              value={values[field.key]}
              onChange={(e) =>
                onChange({ ...values, [field.key]: e.target.value as JevGateMode })
              }
              className="w-full rounded-lg border border-stone-300 px-3 py-2 text-sm focus:border-amber-300 focus:ring focus:ring-amber-200/50"
            >
              {JEV_GATE_MODES.map((mode) => (
                <option key={mode} value={mode}>
                  {MODE_LABEL[mode]}
                </option>
              ))}
            </select>
          </label>
        ))}
      </div>
    </div>
  );
}
