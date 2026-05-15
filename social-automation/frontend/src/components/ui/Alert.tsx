// Copied from social-comment-automation reference; trimmed for solo deploy.
//
// Inline status banner. Each `status` carries a tone (bg/border/text)
// + an icon that matches the alert severity vocabulary.

import type { ReactNode } from "react";

export type AlertStatus = "success" | "info" | "warning" | "error" | "critical";

interface AlertProps {
  status?: AlertStatus;
  title?: string;
  children?: ReactNode;
  className?: string;
}

const STATUS_STYLES: Record<AlertStatus, string> = {
  success: "bg-emerald-50 border-emerald-200 text-emerald-900",
  info: "bg-amber-50 border-amber-200 text-amber-900",
  warning: "bg-amber-50 border-amber-200 text-amber-900",
  error: "bg-rose-50 border-rose-200 text-rose-900",
  critical: "bg-red-50 border-red-300 text-red-900",
};

const STATUS_ICONS: Record<AlertStatus, string> = {
  success: "✓",
  info: "ℹ",
  warning: "⚠",
  error: "✕",
  critical: "‼",
};

export default function Alert({
  status = "info",
  title,
  children,
  className = "",
}: AlertProps) {
  return (
    <div
      role="alert"
      className={`rounded-xl border px-4 py-3 flex items-start gap-3 ${STATUS_STYLES[status]} ${className}`}
    >
      <span className="text-lg leading-none mt-0.5 font-bold" aria-hidden="true">
        {STATUS_ICONS[status]}
      </span>
      <div className="flex-1 min-w-0 text-sm">
        {title && <div className="font-bold mb-1">{title}</div>}
        <div className="whitespace-pre-wrap break-words">{children}</div>
      </div>
    </div>
  );
}
