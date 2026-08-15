/**
 * Dashboard — the "is the machine running, and does it need me" page.
 *
 * It used to show two counts off `/pending` (blog posts, groups to join)
 * and a client-side tally of the last 200 activity rows. Both went dead:
 * FB/IG engagement moved to a single-pass, no-queue design so those
 * pending buckets sit at zero permanently, and hourly `trace` heartbeats
 * flooded the 200-row window so the activity tally under-reported. This
 * version reads the signals that are actually live — flow health, browser
 * sessions, the review queue, and what got published — and every tile
 * links to the page that owns the detail.
 */

import { useMemo } from "react";
import { Link } from "react-router-dom";

import Alert from "../../components/ui/Alert";
import LoadingState from "../../components/ui/LoadingState";
import { endpoints } from "../../api/endpoints";
import { engagementsUrl } from "../../api/engagements";
import { socialPostsUrl } from "../../api/socialPosts";
import { useApiQuery } from "../../hooks/useApiQuery";
import type { EngagementsResponse } from "../../api/engagements";
import type { SessionStatusResponse } from "../../api/sessions";
import type { SocialPostsResponse } from "../../api/socialPosts";
import type { WorkerStatus } from "../../api/workers";
import { Headline, MiniStat, Panel, StatusLine, Tile } from "./cards";
import {
  publishedRows,
  summarizeFlows,
  summarizeSessions,
  summarizeToday,
  type ActivitySummaryResponse,
} from "./summary";

/** Counts + timestamp are all this page reads off `/pending`; typing it
 *  loosely keeps the tile working when a bucket is added or retired. */
interface PendingCounts {
  counts: Record<string, number>;
  as_of: string;
}

const POLL_MS = 15_000;
/** Sessions change on the order of days — no reason to poll them fast. */
const SESSION_POLL_MS = 60_000;
/** Enough to cover a day of publishes many times over: the rate limits cap
 *  the whole estate near 20 actions/day, and rows come newest-first. */
const ENGAGEMENT_LIMIT = 200;

export default function Dashboard(): React.JSX.Element {
  const workersQ = useApiQuery<WorkerStatus[]>(endpoints.workers, {
    refetchInterval: POLL_MS,
  });
  const sessionsQ = useApiQuery<SessionStatusResponse>(endpoints.sessionStatus, {
    refetchInterval: SESSION_POLL_MS,
  });
  const pendingQ = useApiQuery<PendingCounts>(endpoints.pending, {
    refetchInterval: POLL_MS,
  });
  const socialQ = useApiQuery<SocialPostsResponse>(socialPostsUrl("queued"), {
    refetchInterval: POLL_MS,
  });
  const summaryQ = useApiQuery<ActivitySummaryResponse>(
    endpoints.activitySummary(),
    { refetchInterval: POLL_MS },
  );
  const engagementsQ = useApiQuery<EngagementsResponse>(
    engagementsUrl({ limit: ENGAGEMENT_LIMIT }),
    { refetchInterval: POLL_MS },
  );

  const flows = useMemo(
    () => summarizeFlows(workersQ.data ?? []),
    [workersQ.data],
  );
  const sessions = useMemo(
    () => summarizeSessions(sessionsQ.data?.sessions ?? []),
    [sessionsQ.data],
  );
  const engagements = useMemo(
    () => engagementsQ.data?.engagements ?? [],
    [engagementsQ.data],
  );
  const todayCounts = useMemo(
    () => summarizeToday(summaryQ.data, engagements),
    [summaryQ.data, engagements],
  );

  const firstLoad =
    workersQ.loading && !workersQ.data && !pendingQ.data && !summaryQ.data;
  if (firstLoad) return <LoadingState message="Loading dashboard…" />;

  const errors = [
    workersQ.error,
    sessionsQ.error,
    pendingQ.error,
    socialQ.error,
    summaryQ.error,
    engagementsQ.error,
  ].filter(Boolean);

  const inboxTotal = pendingQ.data?.counts.total ?? 0;
  const queuedSocial = socialQ.data?.total ?? 0;
  const awaiting = inboxTotal + queuedSocial;
  const published = publishedRows(engagementsQ.data?.counts ?? {});
  const lastPublished = engagements[0]?.posted_at ?? null;

  return (
    <section className="space-y-6">
      <header className="flex items-baseline justify-between gap-3 flex-wrap">
        <h1 className="text-2xl font-bold text-slate-900">Dashboard</h1>
        <Link
          to="/operations"
          className="text-sm font-medium text-cyan-700 hover:text-cyan-800"
        >
          Open Operations &rarr;
        </Link>
      </header>

      {errors.length > 0 && (
        <Alert status="warning" title="Some panels could not refresh">
          {errors[0]}
        </Alert>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <Tile label="Flows" icon="⚙️" to="/flows" linkText="Flow health →">
          <Headline
            value={
              flows.errored > 0
                ? `${flows.errored} failing`
                : flows.running > 0
                  ? `${flows.running} running`
                  : "All healthy"
            }
            tone={flows.tone}
            caption={`${flows.ok} ok · ${flows.never} never run · ${flows.total} total`}
          />
          {flows.problems.length > 0 && (
            <p className="text-xs text-slate-500 mt-2 break-words">
              {flows.problems.slice(0, 3).join(", ")}
              {flows.problems.length > 3 && ` +${flows.problems.length - 3}`}
            </p>
          )}
        </Tile>

        <Tile label="Browser sessions" icon="🔐" to="/connect" linkText="Connect →">
          {sessions.length === 0 ? (
            <p className="text-sm text-slate-500">No sessions reported.</p>
          ) : (
            sessions.map((s) => (
              <StatusLine
                key={s.platform}
                tone={s.tone}
                label={s.platform}
                value={s.label}
              />
            ))
          )}
          <p className="text-xs text-slate-400 mt-2">last successful run</p>
        </Tile>

        <Tile
          label="Awaiting you"
          icon="📥"
          to={queuedSocial > 0 ? "/social-posts" : "/inbox"}
          linkText={queuedSocial > 0 ? "Review posts →" : "Open Inbox →"}
        >
          <Headline
            value={String(awaiting)}
            tone={awaiting > 0 ? "warn" : "ok"}
            caption={
              awaiting > 0 ? "items need a decision" : "nothing waiting on you"
            }
          />
          <div className="mt-2 space-y-0.5">
            <StatusLine
              tone={queuedSocial > 0 ? "warn" : "idle"}
              label="social posts"
              value={String(queuedSocial)}
            />
            <StatusLine
              tone={inboxTotal > 0 ? "warn" : "idle"}
              label="inbox"
              value={String(inboxTotal)}
            />
          </div>
        </Tile>
      </div>

      <Panel title="Today" to="/activity">
        {summaryQ.error && !summaryQ.data ? (
          <p className="text-sm text-rose-700">{summaryQ.error}</p>
        ) : (
          <>
            {/* Rendered even at zero: the engagement flows fire mid-afternoon,
                so an all-zero morning is the normal state, and a stable grid
                beats a sentence that swaps the layout out from under it. */}
            <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
              <MiniStat label="Comments" value={todayCounts.comments} />
              <MiniStat label="Likes" value={todayCounts.likes} />
              <MiniStat label="Joins" value={todayCounts.joins} />
              <MiniStat label="Posts" value={todayCounts.posts} />
              <MiniStat
                label="Failed"
                value={todayCounts.failed}
                tone={todayCounts.failed > 0 ? "bad" : "idle"}
              />
            </div>
            <p className="text-xs text-slate-400 mt-3">
              {summaryQ.data?.date} UTC — the same day the rate limiter counts
            </p>
          </>
        )}
      </Panel>

      <Panel title="Published to date" to="/published">
        {published.length === 0 ? (
          <p className="text-sm text-slate-500">Nothing published yet.</p>
        ) : (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              {published.map((row) => (
                <MiniStat key={row.key} label={row.label} value={row.value} />
              ))}
            </div>
            {lastPublished && (
              <p className="text-xs text-slate-400 mt-3">
                most recent {new Date(lastPublished).toLocaleString()}
              </p>
            )}
          </>
        )}
      </Panel>
    </section>
  );
}
