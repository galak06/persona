// Reusable confirmation dialog. Backdrop + panel follow the app's only
// overlay precedent (the SideNav mobile drawer): blurred z-40 backdrop that
// closes on click, z-50 centred panel on top.

import { useEffect, useRef } from "react";

export interface DialogAction {
  label: string;
  onClick: () => void;
  variant?: "primary" | "secondary" | "ghost" | "danger";
}

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  children?: React.ReactNode;
  actions: DialogAction[];
  onClose: () => void;
}

// Variants mirror the Reels toolbar buttons: primary = amber Compose,
// secondary = bordered white Refresh, ghost = understated text link.
//
// danger is primary's geometry in the app's destructive hue — rose is what
// every irreversible control already wears (the tile's own Delete link, the
// error Alert). A confirm that undoes something must not read the same as one
// that merely spends a call, so the two carry different colours, not just
// different labels.
const VARIANT_STYLES: Record<NonNullable<DialogAction["variant"]>, string> = {
  primary:
    "px-3 py-1.5 rounded-lg bg-amber-600 text-white text-sm font-semibold hover:bg-amber-700",
  secondary:
    "px-3 py-1.5 rounded-lg border border-brand-border bg-white text-sm font-medium text-slate-700 hover:bg-slate-50",
  ghost: "px-2 py-1.5 text-sm text-slate-500 underline underline-offset-2 hover:text-slate-700",
  danger:
    "px-3 py-1.5 rounded-lg bg-rose-600 text-white text-sm font-semibold hover:bg-rose-700",
};

export default function ConfirmDialog({
  open,
  title,
  children,
  actions,
  onClose,
}: ConfirmDialogProps): React.JSX.Element | null {
  const firstActionRef = useRef<HTMLButtonElement | null>(null);

  // Escape closes the dialog; the listener exists only while it is open.
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (e: KeyboardEvent): void => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  // Focus the first action when opened. A full focus trap (Tab cycling,
  // focus restore on close) is intentionally out of scope for this dialog.
  useEffect(() => {
    if (open) firstActionRef.current?.focus();
  }, [open]);

  if (!open) return null;

  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 z-40 bg-black/30 backdrop-blur-sm" onClick={onClose} />

      {/* Centred panel; the wrapper lets backdrop clicks pass through */}
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4 pointer-events-none">
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="confirm-dialog-title"
          className="bg-white rounded-2xl shadow-floated max-w-sm w-full p-5 pointer-events-auto"
        >
          <h2 id="confirm-dialog-title" className="text-sm font-semibold text-slate-800">
            {title}
          </h2>
          {children && <div className="mt-2 text-sm text-slate-600">{children}</div>}
          <div className="mt-4 flex items-center justify-end gap-2">
            {actions.map((action, i) => (
              <button
                key={action.label}
                type="button"
                ref={i === 0 ? firstActionRef : undefined}
                onClick={action.onClick}
                className={VARIANT_STYLES[action.variant ?? "secondary"]}
              >
                {action.label}
              </button>
            ))}
          </div>
        </div>
      </div>
    </>
  );
}
