import { useEffect, useState } from "react";
import {
  approveRecipe,
  fetchAnalytics,
  rejectRecipe,
  type AffiliateProduct,
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

/** Lists the matched Amazon affiliate products (phase-2) in the detail drawer. */
export function AffiliateProductsSection({
  products,
}: {
  products?: AffiliateProduct[];
}) {
  if (!products || products.length === 0) return null;
  return (
    <div className="mb-4">
      <h3 className="font-medium text-slate-700 mb-1">
        Affiliate products{" "}
        <span className="text-xs font-normal text-slate-400">(Amazon)</span>
      </h3>
      <ul className="text-sm space-y-1">
        {products.map((p) => (
          <li key={p.key} className="flex items-baseline justify-between gap-2">
            <span className="text-slate-700">{p.display}</span>
            <a
              href={`https://www.amazon.com/dp/${p.asin}`}
              target="_blank"
              rel="sponsored nofollow noreferrer"
              className="text-xs text-cyan-700 hover:underline whitespace-nowrap"
            >
              {p.asin} ↗
            </a>
          </li>
        ))}
      </ul>
    </div>
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
