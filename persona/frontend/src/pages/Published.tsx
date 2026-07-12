import { useMemo, useState } from "react";
import {
  engagementsUrl,
  type Engagement,
  type EngagementsResponse,
} from "../api/engagements";
import { useApiQuery } from "../hooks/useApiQuery";
import ErrorState from "../components/ui/ErrorState";
import LoadingState from "../components/ui/LoadingState";

/**
 * Published — the post + comment history recorded in engagements.db by the
 * publish workers (fb_comment, fb_group_post, publish_prepared). Read-only.
 * Filter by platform; each row links out to the live post when a permalink or
 * target URL is known.
 */

const PLATFORMS = ["all", "facebook", "instagram", "wordpress"] as const;
type PlatformFilter = (typeof PLATFORMS)[number];

const KIND_LABEL: Record<string, string> = {
  comment: "Comment",
  link_post: "Group post",
  feed_post: "Feed post",
  reel: "Reel",
  page_post: "Page post",
};

const PLATFORM_ICON: Record<string, string> = {
  facebook: "📘",
  instagram: "📸",
  wordpress: "📝",
};

function statusClasses(status: string): string {
  return status === "posted"
    ? "bg-emerald-50 text-emerald-700"
    : "bg-rose-50 text-rose-700";
}

function linkFor(e: Engagement): string {
  return e.permalink || e.target_url || "";
}

function Row({ e }: { e: Engagement }): React.JSX.Element {
  const href = linkFor(e);
  return (
    <tr className="border-b border-stone-100 hover:bg-stone-50/60 align-top">
      <td className="py-2 pr-3 whitespace-nowrap text-sm">
        <span aria-hidden="true">{PLATFORM_ICON[e.platform] ?? "•"}</span>{" "}
        <span className="text-slate-600">{KIND_LABEL[e.kind] ?? e.kind}</span>
      </td>
      <td className="py-2 pr-3 text-sm text-slate-700">{e.target_name || "—"}</td>
      <td className="py-2 pr-3 text-sm text-slate-600 max-w-md">
        <span className="line-clamp-2">{e.content || (e.error ? `⚠ ${e.error}` : "—")}</span>
      </td>
      <td className="py-2 pr-3 whitespace-nowrap">
        <span className={`rounded px-2 py-0.5 text-xs font-medium ${statusClasses(e.status)}`}>
          {e.status}
        </span>
      </td>
      <td className="py-2 pr-3 whitespace-nowrap text-xs text-slate-400 tabular-nums">
        {e.posted_at ? e.posted_at.slice(0, 16).replace("T", " ") : "—"}
      </td>
      <td className="py-2 text-sm">
        {href ? (
          <a
            href={href}
            target="_blank"
            rel="noreferrer"
            className="text-amber-700 hover:underline"
          >
            open ↗
          </a>
        ) : (
          <span className="text-slate-300">—</span>
        )}
      </td>
    </tr>
  );
}

export default function Published(): React.JSX.Element {
  const [platform, setPlatform] = useState<PlatformFilter>("all");
  const url = useMemo(
    () => engagementsUrl({ platform: platform === "all" ? undefined : platform, limit: 500 }),
    [platform],
  );
  const { data, loading, error, refetch } = useApiQuery<EngagementsResponse>(url);

  const totalPosted = useMemo(() => {
    if (!data) return 0;
    return Object.values(data.counts).reduce((a, b) => a + b, 0);
  }, [data]);

  return (
    <div className="px-8 py-6">
      <header className="mb-5">
        <h1 className="font-display text-2xl font-semibold text-slate-800">Published</h1>
        <p className="text-sm text-slate-500">
          Posts &amp; comments recorded in engagements.db — {totalPosted} posted across platforms.
        </p>
      </header>

      <div className="mb-4 flex flex-wrap gap-2">
        {PLATFORMS.map((p) => (
          <button
            key={p}
            type="button"
            onClick={() => setPlatform(p)}
            className={`rounded-full px-3 py-1 text-sm capitalize transition-colors ${
              platform === p
                ? "bg-amber-600 text-white"
                : "bg-stone-100 text-slate-600 hover:bg-stone-200"
            }`}
          >
            {p}
          </button>
        ))}
      </div>

      {data && Object.keys(data.counts).length > 0 && (
        <div className="mb-4 flex flex-wrap gap-2">
          {Object.entries(data.counts).map(([key, n]) => (
            <span
              key={key}
              className="rounded-md bg-white border border-stone-200 px-2.5 py-1 text-xs text-slate-600"
            >
              {key} <span className="font-semibold tabular-nums">{n}</span>
            </span>
          ))}
        </div>
      )}

      {loading && !data && <LoadingState message="Loading published history…" />}
      {error && (
        <ErrorState message={error} onRetry={() => void refetch()} retrying={loading} />
      )}

      {data && data.engagements.length === 0 && !loading && (
        <p className="text-sm text-slate-400">Nothing published yet for this filter.</p>
      )}

      {data && data.engagements.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-stone-200 bg-white">
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-stone-200 text-xs uppercase tracking-wide text-slate-400">
                <th className="py-2 px-3 font-medium">Kind</th>
                <th className="py-2 pr-3 font-medium">Target</th>
                <th className="py-2 pr-3 font-medium">Content</th>
                <th className="py-2 pr-3 font-medium">Status</th>
                <th className="py-2 pr-3 font-medium">Posted</th>
                <th className="py-2 font-medium">Link</th>
              </tr>
            </thead>
            <tbody className="[&_td:first-child]:pl-3">
              {data.engagements.map((e) => (
                <Row key={e.id} e={e} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
