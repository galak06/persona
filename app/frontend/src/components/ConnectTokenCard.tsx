import Spinner from "./ui/Spinner";
import { formatExpiry } from "./connectFormat";

export interface TokenSummary {
  platform: string;
  token_type: string;
  token_id: string;
  expires_at: string | null;
  needs_refresh: boolean;
  is_expired: boolean;
}

function StatusBadge({ token }: { token: TokenSummary }) {
  if (token.is_expired)
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-red-100 text-red-700 text-xs font-semibold px-2.5 py-1">
        ⚠ Expired
      </span>
    );
  if (token.needs_refresh)
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 text-amber-700 text-xs font-semibold px-2.5 py-1">
        ⏳ Refresh soon
      </span>
    );
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 text-emerald-700 text-xs font-semibold px-2.5 py-1">
      ✓ Active
    </span>
  );
}

interface TokenCardProps {
  token: TokenSummary;
  onRefresh: () => void;
  onDelete: () => void;
  refreshing: boolean;
  deleting: boolean;
}

export default function TokenCard({
  token,
  onRefresh,
  onDelete,
  refreshing,
  deleting,
}: TokenCardProps) {
  const label =
    token.platform === "facebook"
      ? token.token_type === "page"
        ? "Facebook Page Token"
        : "Facebook User Token"
      : "Instagram Token";
  const icon = token.platform === "facebook" ? "📘" : "📷";

  return (
    <div className="bg-brand-surface rounded-2xl border border-brand-border shadow-card p-5 flex flex-col gap-3">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          <span className="text-xl">{icon}</span>
          <div>
            <p className="font-semibold text-slate-800 text-sm">{label}</p>
            {token.token_id && <p className="text-xs text-slate-400">ID: {token.token_id}</p>}
          </div>
        </div>
        <StatusBadge token={token} />
      </div>

      <div className="text-xs text-slate-500">
        Expires: <span className="font-medium text-slate-700">{formatExpiry(token.expires_at)}</span>
      </div>

      <div className="flex gap-2 flex-wrap">
        {(token.needs_refresh || token.is_expired) && token.token_type !== "page" && (
          <button
            onClick={onRefresh}
            disabled={refreshing}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-amber-600 text-white text-xs font-semibold hover:bg-amber-700 disabled:opacity-60 transition-colors"
          >
            {refreshing ? <Spinner size="sm" /> : "↺"} Refresh Token
          </button>
        )}
        <button
          onClick={onDelete}
          disabled={deleting}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-red-200 text-red-600 text-xs font-semibold hover:bg-red-50 disabled:opacity-60 transition-colors"
        >
          {deleting ? <Spinner size="sm" /> : "✕"} Remove
        </button>
      </div>
    </div>
  );
}
