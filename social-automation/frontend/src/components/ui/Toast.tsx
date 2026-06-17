/**
 * Lightweight toast notification system.
 *
 * Usage:
 *   const { toast } = useToast();
 *   toast.success("Worker started", "pid=1234");
 *   toast.error("Already running");
 *   toast.warning("Rate limit hit", "facebook:like 5/5");
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";

// ── Types ─────────────────────────────────────────────────────────────────────

type ToastKind = "success" | "error" | "warning" | "info";

interface ToastItem {
  id: number;
  kind: ToastKind;
  title: string;
  detail?: string;
  duration: number; // ms
}

interface ToastAPI {
  success: (title: string, detail?: string, duration?: number) => void;
  error:   (title: string, detail?: string, duration?: number) => void;
  warning: (title: string, detail?: string, duration?: number) => void;
  info:    (title: string, detail?: string, duration?: number) => void;
}

// ── Context ───────────────────────────────────────────────────────────────────

const ToastContext = createContext<ToastAPI | null>(null);

export function useToast(): { toast: ToastAPI } {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used inside <ToastProvider>");
  return { toast: ctx };
}

// ── Styles ────────────────────────────────────────────────────────────────────

const KIND_STYLES: Record<ToastKind, { bar: string; icon: string; label: string }> = {
  success: { bar: "bg-emerald-500", icon: "✓", label: "text-emerald-700" },
  error:   { bar: "bg-rose-500",    icon: "✕", label: "text-rose-700"    },
  warning: { bar: "bg-amber-400",   icon: "⚠", label: "text-amber-700"  },
  info:    { bar: "bg-sky-500",     icon: "i", label: "text-sky-700"     },
};

// ── Single toast ──────────────────────────────────────────────────────────────

interface ToastCardProps {
  item: ToastItem;
  onDismiss: (id: number) => void;
}

function ToastCard({ item, onDismiss }: ToastCardProps): React.JSX.Element {
  const [visible, setVisible] = useState(false);
  const s = KIND_STYLES[item.kind];

  // Fade in on mount
  useEffect(() => {
    const t = setTimeout(() => setVisible(true), 10);
    return () => clearTimeout(t);
  }, []);

  // Auto-dismiss
  useEffect(() => {
    const t = setTimeout(() => onDismiss(item.id), item.duration);
    return () => clearTimeout(t);
  }, [item.id, item.duration, onDismiss]);

  return (
    <div
      className={`relative overflow-hidden bg-white rounded-lg shadow-lg border border-slate-200 w-80 transition-all duration-300 ${
        visible ? "opacity-100 translate-y-0" : "opacity-0 translate-y-2"
      }`}
    >
      {/* Coloured left bar */}
      <div className={`absolute left-0 top-0 bottom-0 w-1 ${s.bar}`} />

      <div className="flex items-start gap-3 px-4 py-3 pl-5">
        <span className={`text-base font-bold mt-0.5 flex-shrink-0 ${s.label}`}>
          {s.icon}
        </span>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-slate-800 leading-snug">
            {item.title}
          </p>
          {item.detail && (
            <p className="text-xs text-slate-500 mt-0.5 font-mono break-words">
              {item.detail}
            </p>
          )}
        </div>
        <button
          type="button"
          onClick={() => onDismiss(item.id)}
          className="flex-shrink-0 text-slate-300 hover:text-slate-500 text-lg leading-none mt-0.5"
          aria-label="Dismiss"
        >
          ×
        </button>
      </div>

      {/* Progress bar */}
      <div
        className={`h-0.5 ${s.bar} opacity-30`}
        style={{
          animation: `shrink ${item.duration}ms linear forwards`,
        }}
      />
      <style>{`
        @keyframes shrink { from { width: 100% } to { width: 0% } }
      `}</style>
    </div>
  );
}

// ── Provider + Toaster ────────────────────────────────────────────────────────

export function ToastProvider({
  children,
}: {
  children: React.ReactNode;
}): React.JSX.Element {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const nextId = useRef(1);

  const push = useCallback(
    (kind: ToastKind, title: string, detail?: string, duration = 4_000) => {
      const id = nextId.current++;
      setToasts((prev) => [...prev, { id, kind, title, detail, duration }]);
    },
    [],
  );

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const api: ToastAPI = {
    success: (t, d, dur) => push("success", t, d, dur),
    error:   (t, d, dur) => push("error",   t, d, dur),
    warning: (t, d, dur) => push("warning", t, d, dur),
    info:    (t, d, dur) => push("info",    t, d, dur),
  };

  return (
    <ToastContext.Provider value={api}>
      {children}
      {/* Portal-like fixed overlay */}
      <div className="fixed bottom-5 right-5 z-50 flex flex-col gap-2 items-end pointer-events-none">
        {toasts.map((item) => (
          <div key={item.id} className="pointer-events-auto">
            <ToastCard item={item} onDismiss={dismiss} />
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
