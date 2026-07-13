import { endpoints } from "../api/endpoints";
import type { SessionStatus, SessionStatusResponse } from "../api/sessions";
import { useApiQuery } from "../hooks/useApiQuery";
import Alert from "../components/ui/Alert";
import CodeBlock from "../components/ui/CodeBlock";
import ErrorState from "../components/ui/ErrorState";
import LoadingState from "../components/ui/LoadingState";

/**
 * Human Mimic — browser-session (login) status for the active brand.
 *
 * The one thing that actually drives scanning/scouting/commenting: a saved
 * Playwright session per platform. Previously the only way to see this was
 * a copy-paste command shown once, at brand creation — no way to check
 * later whether a session exists or has gone stale. Single-tenant like its
 * Engagement-section siblings (FB Groups, Activity) — shows the API
 * process's active brand, not a picked one.
 */

const PLATFORM_LABELS: Record<string, string> = {
  facebook: "Facebook",
  instagram: "Instagram",
};

function formatLastSaved(iso: string | null): string {
  if (!iso) return "Never logged in";
  const d = new Date(iso);
  const diffMs = Date.now() - d.getTime();
  const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
  if (diffHours < 1) return "Saved less than an hour ago";
  if (diffHours < 24) return `Saved ${diffHours}h ago`;
  const diffDays = Math.floor(diffHours / 24);
  return `Saved ${diffDays}d ago`;
}

function SessionCard({ session }: { session: SessionStatus }): React.JSX.Element {
  const label = PLATFORM_LABELS[session.platform] ?? session.platform;

  return (
    <div className="bg-brand-surface rounded-2xl border border-brand-border shadow-card p-5 flex flex-col gap-3">
      <div className="flex items-center justify-between gap-3">
        <h2 className="font-semibold text-slate-800">{label}</h2>
        {session.exists ? (
          <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 text-emerald-700 text-xs font-semibold px-2.5 py-1">
            ✓ Session saved
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 text-amber-700 text-xs font-semibold px-2.5 py-1">
            ⚠ Not logged in
          </span>
        )}
      </div>

      <p className="text-xs text-slate-500">{formatLastSaved(session.last_saved)}</p>

      <div>
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-400 mb-1">
          {session.exists ? "Re-run to refresh" : "Run once to log in"}
        </p>
        <CodeBlock code={session.login_command} />
      </div>
    </div>
  );
}

export default function HumanMimic(): React.JSX.Element {
  const { data, loading, error, refetch } = useApiQuery<SessionStatusResponse>(
    endpoints.sessionStatus,
  );

  return (
    <div className="px-8 py-6 space-y-6">
      <header className="mb-2">
        <h1 className="font-display text-2xl font-semibold text-slate-800">Human Mimic</h1>
        <p className="text-sm text-slate-500">
          Browser-login sessions that scanning, group-scouting, and commenting run on — this is
          what makes Persona look like a person, not a bot.
        </p>
      </header>

      <Alert status="info">
        Each login opens a real browser window (not headless — FB/IG flag headless agents more
        aggressively). Run the command below once per platform; the session is saved and reused
        by every script until it goes stale.
      </Alert>

      {loading && !data && <LoadingState message="Loading session status…" />}
      {error && (
        <ErrorState message={error} onRetry={() => void refetch()} retrying={loading} />
      )}

      {data && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {data.sessions.map((s) => (
            <SessionCard key={s.platform} session={s} />
          ))}
        </div>
      )}
    </div>
  );
}
