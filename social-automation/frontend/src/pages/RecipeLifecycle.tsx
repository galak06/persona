import { useEffect, useState } from "react";
import {
  approveRecipe,
  fetchAnalytics,
  rejectRecipe,
  type RecipeAnalytics,
} from "../api/recipes";
import { getErrorMessage } from "../api/client";

// Publish-content lifecycle states (mirrors recipe_db.models.ContentStatus).
const STATUS_COLORS: Record<string, string> = {
  generated: "text-sky-600",
  pending: "text-amber-600",
  approved: "text-green-700",
  rejected: "text-red-500",
  published: "text-emerald-700 font-medium",
};

/** Small coloured pill for a recipe's content-lifecycle state. */
export function ContentStatusBadge({ status }: { status?: string }) {
  const s = status || "none";
  if (s === "none") return null;
  return (
    <span className={`block text-xs ${STATUS_COLORS[s] ?? "text-slate-500"}`}>
      ● {s}
    </span>
  );
}

/** Approve/Reject buttons — the phase-5 human gate. Shown only when pending. */
export function ApprovalActions({
  recipeId,
  status,
  onDone,
}: {
  recipeId: string;
  status?: string;
  onDone: () => void;
}) {
  const [busy, setBusy] = useState(false);
  if (status !== "pending") return null;

  async function decide(decision: "approve" | "reject") {
    setBusy(true);
    try {
      if (decision === "approve") await approveRecipe(recipeId);
      else await rejectRecipe(recipeId);
      onDone();
    } catch (err) {
      alert(getErrorMessage(err, "Action failed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="inline-flex gap-1">
      <button
        onClick={(e) => {
          e.stopPropagation();
          void decide("approve");
        }}
        disabled={busy}
        className="px-2 py-0.5 rounded text-xs bg-green-600 text-white hover:bg-green-700 disabled:opacity-50"
      >
        Approve
      </button>
      <button
        onClick={(e) => {
          e.stopPropagation();
          void decide("reject");
        }}
        disabled={busy}
        className="px-2 py-0.5 rounded text-xs bg-red-500 text-white hover:bg-red-600 disabled:opacity-50"
      >
        Reject
      </button>
    </span>
  );
}

/** Compact one-line rollup of publish outcomes (phase-10 analytics). */
export function AnalyticsBar() {
  const [analytics, setAnalytics] = useState<RecipeAnalytics | null>(null);

  useEffect(() => {
    fetchAnalytics()
      .then(setAnalytics)
      .catch(() => setAnalytics(null));
  }, []);

  if (!analytics || analytics.attempts === 0) return null;
  const s = analytics.by_status;
  const parts = [
    s.published ? `${s.published} published` : null,
    s.dry_run ? `${s.dry_run} dry-run` : null,
    s.failed ? `${s.failed} failed` : null,
    s.skipped_rate_limited ? `${s.skipped_rate_limited} rate-limited` : null,
  ].filter(Boolean);
  if (parts.length === 0) return null;
  return <span className="text-xs text-slate-400">· {parts.join(" · ")}</span>;
}
