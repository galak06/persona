// Modal confirmation. The app had no dialog primitive at all — destructive
// actions either fired immediately or leaned on `window.confirm` — so this is
// the shared one: a backdrop, a labelled panel, and a caller-supplied action
// row. Rendering is skipped entirely when `open` is false, which keeps the
// caller's state model to a single "what is pending?" value.

import { useEffect } from "react";
import type { ReactNode } from "react";

export interface DialogAction {
  label: string;
  onClick: () => void;
  variant?: "primary" | "secondary" | "ghost";
}

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  children?: ReactNode;
  actions: DialogAction[];
  onClose: () => void;
}

const VARIANT_STYLES: Record<NonNullable<DialogAction["variant"]>, string> = {
  primary: "bg-rose-600 text-white hover:bg-rose-700",
  secondary: "border border-stone-300 bg-white text-slate-700 hover:bg-stone-50",
  ghost: "text-slate-500 hover:text-slate-700",
};

export default function ConfirmDialog({
  open,
  title,
  children,
  actions,
  onClose,
}: ConfirmDialogProps): React.JSX.Element | null {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div
        className="absolute inset-0 bg-slate-900/40"
        onClick={onClose}
        aria-hidden="true"
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="relative w-full max-w-md rounded-xl border border-stone-200 bg-white p-5 shadow-xl"
      >
        <h2 className="font-display text-lg font-semibold text-slate-800">{title}</h2>
        {children && <div className="mt-2 text-sm text-slate-600">{children}</div>}
        <div className="mt-5 flex items-center justify-end gap-2">
          {actions.map((action) => (
            <button
              key={action.label}
              type="button"
              onClick={action.onClick}
              className={`rounded-lg px-3 py-1.5 text-sm font-semibold ${
                VARIANT_STYLES[action.variant ?? "secondary"]
              }`}
            >
              {action.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
