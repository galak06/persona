import { apiOrigin } from "../api/client";
import { reelVideoUrl } from "../api/ideas";
import type { ContentIdea } from "../api/ideas";

// Beat images are resolved per beat, so "mixed" (some AI, some hero) is a
// normal middle state -- labelling such a reel "Fallback" would misreport it.
// All three are ordinary outcomes: understated styling, never an error look.
const SOURCE_LABELS: Record<string, string> = {
  openart: "AI images",
  mixed: "Partly AI images",
  fallback: "Hero image",
};

function sourceBadge(source: string | null): React.JSX.Element {
  const cls =
    source === "openart" || source === "mixed"
      ? "bg-indigo-50 text-indigo-700"
      : "bg-stone-100 text-slate-600";
  return (
    <span className={`rounded px-2 py-0.5 text-xs font-medium ${cls}`}>
      {(source && SOURCE_LABELS[source]) ?? source ?? "unknown"}
    </span>
  );
}

interface CardProps {
  idea: ContentIdea;
  onDecision: (id: string, status: string) => void;
  busy: boolean;
}

export default function ReelCard({ idea, onDecision, busy }: CardProps): React.JSX.Element {
  const flags = idea.reel_validation_flags ?? [];

  return (
    <div className="bg-brand-surface rounded-2xl border border-brand-border shadow-card p-5 space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-slate-800 leading-snug">{idea.topic}</h3>
          <div className="mt-1">{sourceBadge(idea.reel_source)}</div>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <button
            type="button"
            disabled={busy}
            onClick={() => onDecision(idea.id, "wp_published")}
            className="px-3 py-1.5 rounded border border-stone-200 bg-white text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-40"
          >
            Reject
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => onDecision(idea.id, "social_done")}
            className="px-3 py-1.5 rounded bg-amber-600 text-white text-xs font-semibold hover:bg-amber-700 disabled:opacity-40"
          >
            Approve
          </button>
        </div>
      </div>

      {flags.length > 0 && (
        <div className="rounded-lg bg-rose-50 border border-rose-200 px-3 py-2 text-xs text-rose-700">
          <span className="font-semibold">Flagged:</span> {flags.join(", ")}
        </div>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1.5">
            Instagram
          </div>
          <video
            controls
            className="w-full aspect-[9/16] rounded-lg bg-black object-contain"
            src={reelVideoUrl(apiOrigin, idea.id, "ig")}
          />
          <p className="mt-2 text-xs text-slate-600 whitespace-pre-wrap">{idea.reel_ig_caption}</p>
        </div>
        <div>
          <div className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1.5">
            Facebook
          </div>
          <video
            controls
            className="w-full aspect-[9/16] rounded-lg bg-black object-contain"
            src={reelVideoUrl(apiOrigin, idea.id, "fb")}
          />
          <p className="mt-2 text-xs text-slate-600 whitespace-pre-wrap">{idea.reel_fb_caption}</p>
        </div>
      </div>
    </div>
  );
}
