import { useEffect, useState } from "react";
import {
  CHANNEL_LABELS,
  PUBLISH_CHANNELS,
  artifactUrl,
  fetchArtifacts,
  type ArtifactItem,
  type PublishChannel,
  type RecipeDetail,
} from "../api/recipes";
import { getErrorMessage } from "../api/client";
import { triggerWorker } from "../api/workers";
import { useToast } from "../components/ui/Toast";
import { AffiliateProductsSection } from "./RecipeLifecycle";
import { PagePreviewModal } from "./RecipePagePreview";
import { RecipeMediaSection } from "./RecipeMediaSection";

export function SafetyBadge({ safe }: { safe: boolean }) {
  return safe ? (
    <span className="px-2 py-0.5 rounded-full text-xs bg-green-50 text-green-700">
      dog-safe
    </span>
  ) : (
    <span className="px-2 py-0.5 rounded-full text-xs bg-red-50 text-red-700">
      flagged
    </span>
  );
}

/** True when an IG channel has anything worth showing in the popup. */
function hasIgContent(c?: PublishChannel): boolean {
  return !!c && !!(c.caption || c.reel_url || c.post_url || c.url);
}

/**
 * Four small per-channel publish pills (WP / PDF / IG / FB).
 *
 * The IG pill opens a popup (image post + caption + reel) via `onIgClick`
 * instead of a plain link; the other channels link straight to their URL.
 */
export function PublishBadges({
  status,
  onIgClick,
}: {
  status?: { [key: string]: PublishChannel };
  onIgClick?: (ig: PublishChannel) => void;
}) {
  return (
    <div className="flex gap-1">
      {PUBLISH_CHANNELS.map((ch) => {
        const c = status?.[ch];
        const published = c?.state === "published";
        const label = CHANNEL_LABELS[ch];
        const isIg = ch === "ig";
        const igInteractive = isIg && !!onIgClick && hasIgContent(c);
        const linkClickable =
          published && !!c?.url && !(isIg && !!onIgClick);
        const clickable = igInteractive || linkClickable;
        const pill = (
          <span
            className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
              published
                ? "bg-green-100 text-green-700"
                : "bg-slate-100 text-slate-400"
            } ${clickable ? "cursor-pointer hover:bg-green-200 underline decoration-dotted" : ""}`}
          >
            {label}
          </span>
        );
        if (igInteractive && c) {
          return (
            <button
              key={ch}
              type="button"
              title={`${label} — view post, caption & reel`}
              onClick={(e) => {
                e.stopPropagation();
                onIgClick?.(c);
              }}
              className="appearance-none border-0 bg-transparent p-0"
            >
              {pill}
            </button>
          );
        }
        return (
          <span
            key={ch}
            title={`${label} ${published ? "published" : "not published"}`}
          >
            {linkClickable && c?.url ? (
              <a
                href={c.url}
                target="_blank"
                rel="noreferrer"
                onClick={(e) => e.stopPropagation()}
              >
                {pill}
              </a>
            ) : (
              pill
            )}
          </span>
        );
      })}
    </div>
  );
}

/** A single link/placeholder row inside the IG popup. */
function IgLink({
  icon,
  label,
  url,
}: {
  icon: string;
  label: string;
  url: string;
}) {
  if (url) {
    return (
      <a
        href={url}
        target="_blank"
        rel="noreferrer"
        className="flex items-center gap-2 rounded-md bg-pink-50 px-3 py-2 text-sm text-pink-700 hover:bg-pink-100"
      >
        <span>{icon}</span>
        {label} ↗
      </a>
    );
  }
  return (
    <span className="flex items-center gap-2 rounded-md bg-slate-50 px-3 py-2 text-sm text-slate-400">
      <span>{icon}</span>
      {label} — not posted yet
    </span>
  );
}

/** Popup showing the IG image post + reel links and the drafted caption. */
export function IgModal({
  ig,
  onClose,
}: {
  ig: PublishChannel;
  onClose: () => void;
}) {
  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center"
      onClick={onClose}
    >
      <div className="absolute inset-0 bg-black/40" aria-hidden />
      <div
        className="relative z-[61] m-4 w-full max-w-md max-h-[85vh] overflow-y-auto rounded-lg bg-white p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <h3 className="flex items-center gap-2 font-semibold text-slate-800">
            <span>📸</span> Instagram
          </h3>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-600"
          >
            ✕
          </button>
        </div>

        <div className="mb-4 flex flex-col gap-2">
          <IgLink icon="🖼️" label="Image post" url={ig.post_url} />
          <IgLink icon="🎬" label="Reel" url={ig.reel_url || ig.url} />
        </div>

        <div className="text-xs font-medium text-slate-500 mb-1">Caption</div>
        {ig.caption ? (
          <pre className="whitespace-pre-wrap rounded bg-slate-50 p-3 font-sans text-sm text-slate-700">
            {ig.caption}
          </pre>
        ) : (
          <div className="text-sm text-slate-400">No caption drafted yet.</div>
        )}
      </div>
    </div>
  );
}

/** Popup listing every artifact file for a recipe, each linked (image = thumb). */
export function ArtifactsModal({
  recipeId,
  recipeName,
  onClose,
}: {
  recipeId: string;
  recipeName: string;
  onClose: () => void;
}) {
  const [items, setItems] = useState<ArtifactItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    fetchArtifacts(recipeId)
      .then((r) => live && setItems(r.artifacts))
      .catch((e) => live && setError(getErrorMessage(e)));
    return () => {
      live = false;
    };
  }, [recipeId]);

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center"
      onClick={onClose}
    >
      <div className="absolute inset-0 bg-black/40" aria-hidden />
      <div
        className="relative z-[61] m-4 w-full max-w-lg max-h-[85vh] overflow-y-auto rounded-lg bg-white p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <h3 className="flex items-center gap-2 font-semibold text-slate-800">
            <span>📁</span> Artifacts — {recipeName}
          </h3>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-600"
          >
            ✕
          </button>
        </div>

        {error && <div className="text-sm text-rose-600">{error}</div>}
        {!items && !error && (
          <div className="text-sm text-slate-400">Loading…</div>
        )}
        {items && items.length === 0 && (
          <div className="text-sm text-slate-400">
            No artifacts on disk for this recipe yet.
          </div>
        )}

        <div className="grid grid-cols-2 gap-3">
          {items?.map((a) => {
            const url = artifactUrl(recipeId, a.path);
            return (
              <a
                key={a.path}
                href={url}
                target="_blank"
                rel="noreferrer"
                className="block rounded border border-slate-200 p-2 hover:border-cyan-400"
              >
                {a.kind === "image" ? (
                  <img
                    src={url}
                    alt={a.name}
                    className="h-28 w-full rounded object-cover"
                  />
                ) : (
                  <div className="flex h-28 items-center justify-center rounded bg-slate-50 text-3xl">
                    {a.kind === "pdf" ? "📄" : a.kind === "json" ? "🧾" : "📦"}
                  </div>
                )}
                <div
                  className="mt-1 truncate text-xs text-slate-600"
                  title={a.path}
                >
                  {a.path}
                </div>
                <div className="text-[10px] text-slate-400">
                  {(a.size / 1024).toFixed(0)} KB
                </div>
              </a>
            );
          })}
        </div>
      </div>
    </div>
  );
}

export function RecipeDrawer({
  recipe,
  loading,
  onClose,
}: {
  recipe: RecipeDetail | null;
  loading: boolean;
  onClose: () => void;
}) {
  const [igModal, setIgModal] = useState<PublishChannel | null>(null);
  const [showPreview, setShowPreview] = useState(false);
  const [imgLoading, setImgLoading] = useState(false);
  const { toast } = useToast();
  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <div
        className="absolute inset-0 bg-black/30"
        onClick={onClose}
        aria-hidden
      />
      <div className="relative z-50 w-full max-w-md bg-white h-full overflow-y-auto shadow-xl p-6">
        <button
          onClick={onClose}
          className="absolute top-4 right-4 text-slate-400 hover:text-slate-600"
        >
          ✕
        </button>
        {loading || !recipe ? (
          <div className="text-slate-500">Loading…</div>
        ) : (
          <div>
            <h2 className="text-lg font-semibold text-slate-800 pr-6">
              {recipe.display_name || recipe.name}
            </h2>
            {recipe.display_name && (
              <div className="text-xs text-slate-400 mt-0.5">
                source title: {recipe.name}
              </div>
            )}
            <div className="flex items-center gap-3 mt-1 mb-3 text-sm text-slate-500">
              <SafetyBadge safe={recipe.dog_safe === true} />
              {recipe.category && <span>{recipe.category}</span>}
            </div>

            <div className="mb-4 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => setShowPreview(true)}
                className="inline-flex items-center gap-1.5 rounded-md bg-amber-500 px-3 py-1.5 text-sm font-medium text-white hover:bg-amber-600"
              >
                🔍 Preview page
              </button>
              {recipe.wp_url && (
                <>
                  <button
                    type="button"
                    disabled={imgLoading}
                    onClick={() => {
                      setImgLoading(true);
                      triggerWorker("dogfood-worker-image", { recipeIds: [recipe.id] })
                        .then(() => {
                          toast.success("Image generation queued");
                        })
                        .catch((err: unknown) => {
                          toast.error(
                            "Failed to queue image generation",
                            getErrorMessage(err),
                          );
                        })
                        .finally(() => {
                          setImgLoading(false);
                        });
                    }}
                    className="inline-flex items-center gap-1.5 rounded-md bg-violet-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-violet-700 disabled:opacity-50"
                  >
                    {imgLoading ? "Generating..." : "🖼️ Generate Image"}
                  </button>
                  {recipe.card_html_path && (
                    <button
                      type="button"
                      onClick={() =>
                        window.open(`file://${recipe.card_html_path}`, "_blank")
                      }
                      className="inline-flex items-center gap-1.5 rounded-md bg-slate-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700"
                    >
                      🖼 View HTML
                    </button>
                  )}
                </>
              )}
            </div>

            <RecipeMediaSection recipeId={recipe.id} media={recipe.media} />

            <h3 className="font-medium text-slate-700 mb-1">Publishing</h3>
            <div className="mb-2">
              <PublishBadges
                status={recipe.publish_status}
                onIgClick={setIgModal}
              />
            </div>
            <ul className="text-xs text-slate-500 mb-4 space-y-0.5">
              {PUBLISH_CHANNELS.map((ch) => {
                const c = recipe.publish_status?.[ch];
                const published = c?.state === "published";
                return (
                  <li key={ch}>
                    {CHANNEL_LABELS[ch]}:{" "}
                    {published ? (
                      c?.url ? (
                        <a
                          href={c.url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-cyan-700 hover:underline"
                        >
                          published{c.at ? ` · ${c.at.slice(0, 10)}` : ""}
                        </a>
                      ) : (
                        <span className="text-green-700">published</span>
                      )
                    ) : (
                      <span className="text-slate-400">not published</span>
                    )}
                  </li>
                );
              })}
            </ul>

            <AffiliateProductsSection products={recipe.affiliate_products} />

            {recipe.toxic_flags && recipe.toxic_flags.length > 0 && (
              <div className="mb-4 bg-red-50 text-red-700 text-sm p-2 rounded">
                ⚠️ Toxic for dogs: {recipe.toxic_flags.join(", ")}
              </div>
            )}
            <div className="text-sm text-slate-500 mb-4">
              prep {recipe.prep_minutes ?? 0}m · cook {recipe.cook_minutes ?? 0}m
              {recipe.servings ? ` · ${recipe.servings}` : ""}
            </div>

            {recipe.artifacts_path && (
              <div className="text-xs text-slate-500 mb-4 break-all">
                Artifacts:{" "}
                <a
                  href={`file://${recipe.artifacts_path}`}
                  className="text-cyan-700 hover:underline"
                >
                  {recipe.artifacts_path}
                </a>
              </div>
            )}

            {recipe.card_path && (
              <div className="text-xs mb-4 break-all">
                <span className="inline-block px-2 py-0.5 rounded-full bg-violet-50 text-violet-700">
                  🖼️ recipe card created
                </span>{" "}
                <a
                  href={`file://${recipe.card_path}`}
                  className="text-cyan-700 hover:underline"
                >
                  open file
                </a>
                {recipe.card_created_at && (
                  <span className="text-slate-400">
                    {" "}
                    · {recipe.card_created_at.slice(0, 16)}
                  </span>
                )}
              </div>
            )}

            <h3 className="font-medium text-slate-700 mb-1">Ingredients</h3>
            <ul className="list-disc pl-5 text-sm text-slate-600 mb-4 space-y-0.5">
              {(recipe.ingredients ?? []).map((ing, i) => (
                <li key={i}>
                  {[ing.qty, ing.unit, ing.item].filter(Boolean).join(" ")}
                  {ing.notes ? `, ${ing.notes}` : ""}
                </li>
              ))}
            </ul>

            <h3 className="font-medium text-slate-700 mb-1">Steps</h3>
            <ol className="list-decimal pl-5 text-sm text-slate-600 mb-4 space-y-1">
              {(recipe.steps ?? []).map((step, i) => (
                <li key={i}>{step}</li>
              ))}
            </ol>

            {recipe.source_url && (
              <a
                href={recipe.source_url}
                target="_blank"
                rel="noreferrer"
                className="text-sm text-cyan-700 hover:underline"
              >
                View original on {recipe.source_name} ↗
              </a>
            )}
          </div>
        )}
      </div>
      {igModal && (
        <IgModal ig={igModal} onClose={() => setIgModal(null)} />
      )}
      {showPreview && recipe && (
        <PagePreviewModal
          recipeId={recipe.id}
          recipeName={recipe.display_name || recipe.name}
          onClose={() => setShowPreview(false)}
        />
      )}
    </div>
  );
}
