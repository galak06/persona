// Copied from social-comment-automation reference; trimmed for solo deploy.
//
// Page-level loading shim. For inline button spinners use `<Spinner>`.

interface LoadingStateProps {
  message?: string;
  className?: string;
}

export default function LoadingState({
  message = "Loading…",
  className = "",
}: LoadingStateProps) {
  return (
    <div
      role="status"
      aria-live="polite"
      className={`p-12 text-center text-slate-500 font-medium ${className}`}
    >
      <div className="flex justify-center mb-3">
        <svg
          className="animate-spin h-6 w-6 text-amber-600"
          fill="none"
          viewBox="0 0 24 24"
          aria-hidden="true"
        >
          <circle
            className="opacity-25"
            cx="12"
            cy="12"
            r="10"
            stroke="currentColor"
            strokeWidth="4"
          />
          <path
            className="opacity-75"
            fill="currentColor"
            d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
          />
        </svg>
      </div>
      <span>{message}</span>
    </div>
  );
}
