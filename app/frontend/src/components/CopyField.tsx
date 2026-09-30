/**
 * A labelled, read-only value with a Copy button.
 *
 * Built for the spotlight DM links, which are the one thing on the review
 * page the operator has to move OUT of the app by hand: the affiliate URL
 * never goes in the caption, it gets pasted into a DM when someone comments
 * the keyword. Selecting a long URL out of a paragraph is exactly the step
 * that produces a truncated link, so the value sits in its own input and the
 * button copies the whole thing.
 *
 * `note` renders underneath in muted text — the affiliate disclosure travels
 * with the link, and a copy control that leaves it behind invites sending
 * the link without it.
 *
 * `CodeBlock` already copies, but it is a dark terminal block for shell
 * commands; this is a form field, so it wears the form's clothes instead.
 */

import { useId } from "react";
import { useToast } from "./ui/Toast";

export interface CopyFieldProps {
  label: string;
  value: string;
  /** Muted line under the field — e.g. the affiliate disclosure sentence. */
  note?: string;
}

export default function CopyField({
  label,
  value,
  note,
}: CopyFieldProps): React.JSX.Element {
  const { toast } = useToast();
  const inputId = useId();

  const handleCopy = async (): Promise<void> => {
    try {
      // Throws in an insecure context, and is simply absent in a few
      // embedded browsers — either way the value stays visible and
      // selectable, so the failure is worth naming but not worth blocking on.
      await navigator.clipboard.writeText(value);
      toast.success(`${label} copied`);
    } catch {
      toast.error(
        "Could not copy",
        "Select the link and copy it manually — the clipboard is unavailable here.",
      );
    }
  };

  return (
    <div className="space-y-1">
      <label
        htmlFor={inputId}
        className="block text-xs font-semibold uppercase tracking-wider text-slate-400"
      >
        {label}
      </label>
      <div className="flex items-center gap-2">
        <input
          id={inputId}
          type="text"
          readOnly
          value={value}
          className="min-w-0 flex-1 rounded border border-brand-border bg-stone-50 px-2 py-1 font-mono text-xs text-slate-700"
        />
        <button
          type="button"
          onClick={() => void handleCopy()}
          aria-label={`Copy ${label}`}
          className="shrink-0 rounded border border-brand-border bg-white px-2.5 py-1 text-xs font-semibold text-slate-700 hover:bg-slate-50"
        >
          Copy
        </button>
      </div>
      {note && <p className="text-[11px] leading-snug text-slate-500">{note}</p>}
    </div>
  );
}
