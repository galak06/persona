/**
 * "Keywords" tab on the Ideas page — the vocabulary the scout searches.
 *
 * Split into its own file rather than added inline: Ideas.tsx is already
 * near 400 lines, and this tab shares nothing with the ideas table beyond
 * sitting behind the same tab strip.
 *
 * The distinction the page exists to make is curated vs discovered. Curated
 * terms are the operator's own `content_analysis.keywords` and never change
 * by themselves. Discovered terms are what the Trends stage found on past
 * runs, and they accumulate — which is the whole point, but it also means
 * the vocabulary can drift without anyone seeing it. Hence a viewer.
 */

import { useApiQuery } from "../hooks/useApiQuery";
import { KEYWORDS_URL, type KeywordsResponse } from "../api/keywords";
import ErrorState from "../components/ui/ErrorState";
import LoadingState from "../components/ui/LoadingState";
import EmptyState from "../components/ui/EmptyState";

function shortDate(iso: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toISOString().slice(0, 10);
}

export default function IdeasKeywords(): React.JSX.Element {
  const { data, loading, error, refetch } = useApiQuery<KeywordsResponse>(KEYWORDS_URL);

  if (loading && !data) return <LoadingState message="Loading keywords…" />;
  if (error && !data)
    return (
      <ErrorState
        title="Could not load keywords"
        message={error}
        onRetry={() => void refetch()}
        retrying={loading}
      />
    );

  const curated = data?.curated ?? [];
  const discovered = data?.discovered ?? [];
  const activeCount = discovered.filter((k) => k.active).length;
  const limit = data?.active_limit ?? 0;

  return (
    <div className="space-y-5">
      <div className="bg-brand-surface rounded-2xl border border-brand-border shadow-card px-5 py-4 flex flex-wrap items-baseline gap-x-6 gap-y-2">
        <div className="flex items-baseline gap-2">
          <span className="text-2xl font-bold text-slate-900 leading-none">
            {curated.length + activeCount}
          </span>
          <span className="text-sm text-slate-500">terms in the search vocabulary</span>
        </div>
        <span className="text-xs text-slate-500">
          {curated.length} curated · {activeCount} of {discovered.length} discovered active
          {discovered.length > activeCount && (
            <span className="text-slate-400"> (top {limit} by score)</span>
          )}
        </span>
      </div>

      <section className="bg-brand-surface rounded-2xl border border-brand-border shadow-card overflow-hidden">
        <header className="px-5 py-3 border-b border-brand-border">
          <h2 className="text-sm font-semibold text-slate-800">Discovered by the scout</h2>
          <p className="text-xs text-slate-500 mt-0.5">
            Found by the Trends stage from Google Search Console, web search, and the Instagram
            trends feed. Kept on file so each run searches wider than the last.
          </p>
        </header>
        {discovered.length === 0 ? (
          <EmptyState
            title="Nothing discovered yet"
            description="The next Generate run records what its research finds; the widening starts from the run after that."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="text-left font-medium px-5 py-2">Keyword</th>
                  <th className="text-left font-medium px-3 py-2">Category</th>
                  <th className="text-right font-medium px-3 py-2">Score</th>
                  <th className="text-right font-medium px-3 py-2">Seen</th>
                  <th className="text-left font-medium px-3 py-2">First</th>
                  <th className="text-left font-medium px-3 py-2">Last</th>
                  <th className="text-left font-medium px-5 py-2">Why</th>
                </tr>
              </thead>
              <tbody>
                {discovered.map((k) => (
                  <tr
                    key={k.keyword}
                    className={`border-t border-brand-border ${k.active ? "" : "opacity-55"}`}
                  >
                    <td className="px-5 py-2 font-medium text-slate-800">
                      <span className="flex items-center gap-2">
                        {k.keyword}
                        {!k.active && (
                          <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-slate-100 text-slate-500 border border-slate-200">
                            on file
                          </span>
                        )}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-slate-600">{k.category}</td>
                    <td className="px-3 py-2 text-right tabular-nums text-slate-700">
                      {k.best_score.toFixed(0)}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-slate-600">
                      {k.times_seen}
                    </td>
                    <td className="px-3 py-2 text-slate-500 whitespace-nowrap">
                      {shortDate(k.first_seen)}
                    </td>
                    <td className="px-3 py-2 text-slate-500 whitespace-nowrap">
                      {shortDate(k.last_seen)}
                    </td>
                    <td className="px-5 py-2 text-slate-500 max-w-md">{k.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="bg-brand-surface rounded-2xl border border-brand-border shadow-card overflow-hidden">
        <header className="px-5 py-3 border-b border-brand-border">
          <h2 className="text-sm font-semibold text-slate-800">Curated in brand settings</h2>
          <p className="text-xs text-slate-500 mt-0.5">
            From <code className="text-[11px]">content_analysis.keywords</code>. Always active, and
            only ever changed by you — the scout never writes here, because the Facebook and
            Instagram scanners score posts against this same list.
          </p>
        </header>
        {curated.length === 0 ? (
          <EmptyState title="No curated keywords" description="Add them in brand settings." />
        ) : (
          <div className="px-5 py-4 flex flex-wrap gap-1.5">
            {curated.map((k) => (
              <span
                key={`${k.category}:${k.keyword}`}
                title={k.category}
                className="text-xs px-2 py-1 rounded-full bg-slate-100 text-slate-700 border border-slate-200"
              >
                {k.keyword}
              </span>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
