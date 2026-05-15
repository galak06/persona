// Copied from social-comment-automation reference; trimmed for solo deploy.
//
// Contextful empty-state block. Centered, with optional preview and
// action slots.

import type { ReactNode } from "react";

interface EmptyStateProps {
  title: string;
  description?: string;
  /** Render before the title — e.g., a faded ghost-card preview or icon. */
  preview?: ReactNode;
  /** Render after the description — e.g., a refresh button. */
  actions?: ReactNode;
  className?: string;
}

export default function EmptyState({
  title,
  description,
  preview,
  actions,
  className = "",
}: EmptyStateProps) {
  return (
    <div className={`p-8 sm:p-12 ${className}`}>
      {preview && <div className="mb-6">{preview}</div>}
      <div className="text-center">
        <p className="text-title text-slate-900 mb-1">{title}</p>
        {description && (
          <p className="text-sm text-slate-600 max-w-sm mx-auto">{description}</p>
        )}
        {actions && <div className="mt-6">{actions}</div>}
      </div>
    </div>
  );
}
