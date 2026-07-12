/**
 * Connect page — FB + IG OAuth setup in one place.
 *
 * Flow:
 *   1. "Connect Facebook" → redirects to FB OAuth consent via the API
 *   2. FB redirects back to /api/v1/oauth/facebook/callback (handled server-side)
 *   3. User enters their Page ID → "Get Page Token" fetches a non-expiring page token
 *   4. IG uses the same page token — no separate flow needed
 *   5. Expiring tokens show a "Refresh" button
 */

import { useState } from "react";
import { useApiQuery } from "../hooks/useApiQuery";
import { useApiMutation } from "../hooks/useApiMutation";
import { endpoints } from "../api/endpoints";
import Alert from "../components/ui/Alert";
import ErrorState from "../components/ui/ErrorState";
import Spinner from "../components/ui/Spinner";

interface TokenSummary {
  platform: string;
  token_type: string;
  token_id: string;
  expires_at: string | null;
  needs_refresh: boolean;
  is_expired: boolean;
}

interface TokensResponse {
  brand_id: string;
  tokens: TokenSummary[];
}

const API_BASE = "/api/v1";

function formatExpiry(iso: string | null): string {
  if (!iso) return "Never (page token)";
  const d = new Date(iso);
  const diff = Math.ceil((d.getTime() - Date.now()) / (1000 * 60 * 60 * 24));
  if (diff < 0) return `Expired ${Math.abs(diff)}d ago`;
  return `${d.toLocaleDateString()} (${diff}d left)`;
}

function StatusBadge({ token }: { token: TokenSummary }) {
  if (token.is_expired)
    return <span className="inline-flex items-center gap-1 rounded-full bg-red-100 text-red-700 text-xs font-semibold px-2.5 py-1">⚠ Expired</span>;
  if (token.needs_refresh)
    return <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 text-amber-700 text-xs font-semibold px-2.5 py-1">⏳ Refresh soon</span>;
  return <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 text-emerald-700 text-xs font-semibold px-2.5 py-1">✓ Active</span>;
}

function TokenCard({
  token,
  onRefresh,
  onDelete,
  refreshing,
  deleting,
}: {
  token: TokenSummary;
  onRefresh: () => void;
  onDelete: () => void;
  refreshing: boolean;
  deleting: boolean;
}) {
  const label =
    token.platform === "facebook"
      ? token.token_type === "page" ? "Facebook Page Token" : "Facebook User Token"
      : "Instagram Token";
  const icon = token.platform === "facebook" ? "📘" : "📷";

  return (
    <div className="bg-brand-surface rounded-2xl border border-brand-border shadow-card p-5 flex flex-col gap-3">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          <span className="text-xl">{icon}</span>
          <div>
            <p className="font-semibold text-slate-800 text-sm">{label}</p>
            {token.token_id && (
              <p className="text-xs text-slate-400">ID: {token.token_id}</p>
            )}
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

export default function Connect() {
  const [pageId, setPageId] = useState("");
  const [pageMsg, setPageMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [refreshMsg, setRefreshMsg] = useState<string | null>(null);

  const { data, loading, error, refetch } = useApiQuery<TokensResponse>(endpoints.oauthTokens);
  const pageTokenMut = useApiMutation<{ status: string; message: string }>("post");
  const refreshMut = useApiMutation<{ refreshed: boolean; new_expires_at: string | null }>("post");
  const deleteMut = useApiMutation<{ status: string }>("delete");

  const tokens = data?.tokens ?? [];
  const fbUserToken = tokens.find(t => t.platform === "facebook" && t.token_type === "bearer");
  const fbPageToken = tokens.find(t => t.platform === "facebook" && t.token_type === "page");

  const handleConnectFacebook = () => {
    window.location.href = `${API_BASE}${endpoints.oauthFacebookStart}`;
  };

  const handleGetPageToken = async () => {
    if (!pageId.trim()) return;
    setPageMsg(null);
    const result = await pageTokenMut.mutate(endpoints.oauthFacebookPage(pageId.trim()));
    if (result && !pageTokenMut.error) {
      setPageMsg({ ok: true, text: "Page token saved — Facebook and Instagram are ready." });
      refetch?.();
    } else {
      setPageMsg({ ok: false, text: pageTokenMut.error || "Failed to get page token." });
    }
  };

  const handleRefresh = async () => {
    setRefreshMsg(null);
    const result = await refreshMut.mutate(endpoints.oauthFacebookRefresh);
    if (result) {
      setRefreshMsg(result.refreshed ? "Token refreshed successfully." : "Token still valid — no refresh needed.");
      refetch?.();
    }
  };

  const handleDelete = async (token: TokenSummary) => {
    await deleteMut.mutate(endpoints.oauthDelete(token.platform, token.token_type));
    refetch?.();
  };

  return (
    <div className="space-y-8 max-w-2xl">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Connect Accounts</h1>
        <p className="mt-1 text-sm text-slate-500">
          One OAuth flow connects both Facebook and Instagram. Facebook Page token = IG publisher token.
        </p>
      </div>

      {/* Status messages */}
      {refreshMsg && (
        <Alert status="success">{refreshMsg}</Alert>
      )}
      {error && (
        <ErrorState message={`Could not load token status: ${error}`} onRetry={() => void refetch()} retrying={loading} />
      )}

      {/* ── Step 1: Connect Facebook ─────────────────────────────────── */}
      <section className="bg-brand-surface rounded-2xl border border-brand-border shadow-card p-6 space-y-4">
        <div className="flex items-center gap-3">
          <span className="text-2xl">📘</span>
          <div>
            <h2 className="font-semibold text-slate-800">Step 1 — Connect Facebook</h2>
            <p className="text-xs text-slate-500 mt-0.5">
              Grants access to post to your Page and manage Instagram via the Graph API.
            </p>
          </div>
        </div>

        {fbUserToken && !fbUserToken.is_expired ? (
          <div className="flex items-center gap-2 text-sm text-emerald-700 bg-emerald-50 rounded-xl px-4 py-3">
            <span>✓</span>
            <span>Facebook user token active — expires {formatExpiry(fbUserToken.expires_at)}</span>
          </div>
        ) : (
          <button
            onClick={handleConnectFacebook}
            className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-[#1877F2] text-white text-sm font-semibold hover:bg-[#166FE5] transition-colors shadow-sm"
          >
            <span>f</span> Connect with Facebook
          </button>
        )}
        {fbUserToken?.is_expired && (
          <div className="space-y-2">
            <Alert status="warning">User token expired — reconnect to restore access.</Alert>
            <button
              onClick={handleConnectFacebook}
              className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-[#1877F2] text-white text-sm font-semibold hover:bg-[#166FE5] transition-colors"
            >
              Reconnect Facebook
            </button>
          </div>
        )}
      </section>

      {/* ── Step 2: Get Page Token ───────────────────────────────────── */}
      <section className={`bg-brand-surface rounded-2xl border shadow-card p-6 space-y-4 transition-opacity ${fbUserToken && !fbUserToken.is_expired ? "border-brand-border opacity-100" : "border-brand-border opacity-40 pointer-events-none"}`}>
        <div className="flex items-center gap-3">
          <span className="text-2xl">🔑</span>
          <div>
            <h2 className="font-semibold text-slate-800">Step 2 — Get Page Token</h2>
            <p className="text-xs text-slate-500 mt-0.5">
              Converts the user token into a non-expiring Page token used for all publishing.
              Find your Page ID in Facebook Page Settings → About.
            </p>
          </div>
        </div>

        {fbPageToken ? (
          <div className="flex items-center gap-2 text-sm text-emerald-700 bg-emerald-50 rounded-xl px-4 py-3">
            <span>✓</span>
            <span>Page token active — does not expire</span>
          </div>
        ) : (
          <div className="flex gap-2">
            <input
              type="text"
              value={pageId}
              onChange={e => setPageId(e.target.value)}
              placeholder="Facebook Page ID (e.g. 123456789)"
              className="flex-1 min-w-0 rounded-xl border border-brand-border px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-500/40"
            />
            <button
              onClick={handleGetPageToken}
              disabled={!pageId.trim() || pageTokenMut.loading}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-brand-primary text-white text-sm font-semibold hover:bg-brand-primary-hover disabled:opacity-60 transition-colors whitespace-nowrap"
            >
              {pageTokenMut.loading ? <Spinner size="sm" /> : "Get Token"}
            </button>
          </div>
        )}

        {pageMsg && (
          <Alert status={pageMsg.ok ? "success" : "error"}>
            {pageMsg.text}
          </Alert>
        )}
      </section>

      {/* ── Step 3: Instagram ────────────────────────────────────────── */}
      <section className={`bg-brand-surface rounded-2xl border shadow-card p-6 space-y-3 transition-opacity ${fbPageToken ? "border-brand-border opacity-100" : "border-brand-border opacity-40"}`}>
        <div className="flex items-center gap-3">
          <span className="text-2xl">📷</span>
          <div>
            <h2 className="font-semibold text-slate-800">Instagram</h2>
            <p className="text-xs text-slate-500 mt-0.5">
              No separate flow needed. Instagram Business uses the same Facebook Page token.
            </p>
          </div>
        </div>
        {fbPageToken ? (
          <div className="flex items-center gap-2 text-sm text-emerald-700 bg-emerald-50 rounded-xl px-4 py-3">
            <span>✓</span>
            <span>Instagram ready via Facebook Page token</span>
          </div>
        ) : (
          <div className="text-sm text-slate-400 italic">Complete Steps 1 + 2 first</div>
        )}
      </section>

      {/* ── Active tokens ────────────────────────────────────────────── */}
      {tokens.length > 0 && (
        <section className="space-y-3">
          <h2 className="font-semibold text-slate-700 text-sm uppercase tracking-wider">
            Stored Tokens
          </h2>
          {loading && <Spinner />}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {tokens.map(t => (
              <TokenCard
                key={`${t.platform}-${t.token_type}-${t.token_id}`}
                token={t}
                onRefresh={() => handleRefresh()}
                onDelete={() => handleDelete(t)}
                refreshing={refreshMut.loading}
                deleting={deleteMut.loading}
              />
            ))}
          </div>
        </section>
      )}

      {!loading && tokens.length === 0 && !error && (
        <div className="text-center py-10 text-slate-400 text-sm">
          No tokens stored yet — complete Step 1 to connect.
        </div>
      )}
    </div>
  );
}
