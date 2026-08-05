// Page/section-level fetch-error block: the shared `Alert` plus a Retry
// button wired to the caller's `refetch()`. Every `useApiQuery` result
// already carries `refetch` — this is what most pages were missing to use
// it: an error today just renders text with no way to recover except a
// full page reload.

import Alert from "./Alert";

interface ErrorStateProps {
  message: string;
  title?: string;
  onRetry?: () => void;
  retrying?: boolean;
  className?: string;
}

export default function ErrorState({
  message,
  title,
  onRetry,
  retrying = false,
  className = "",
}: ErrorStateProps): React.JSX.Element {
  return (
    <Alert status="error" title={title} className={className}>
      <div className="flex items-center justify-between gap-3">
        <span>{message}</span>
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            disabled={retrying}
            className="shrink-0 rounded-lg border border-rose-300 bg-white px-2.5 py-1 text-xs font-semibold text-rose-700 hover:bg-rose-50 disabled:opacity-50"
          >
            {retrying ? "Retrying…" : "Retry"}
          </button>
        )}
      </div>
    </Alert>
  );
}
