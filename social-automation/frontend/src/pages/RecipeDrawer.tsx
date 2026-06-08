import {
  CHANNEL_LABELS,
  PUBLISH_CHANNELS,
  type PublishChannel,
  type RecipeDetail,
} from "../api/recipes";

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

/** Four small per-channel publish pills (WP / PDF / IG / FB). */
export function PublishBadges({
  status,
}: {
  status?: { [key: string]: PublishChannel };
}) {
  return (
    <div className="flex gap-1">
      {PUBLISH_CHANNELS.map((ch) => {
        const c = status?.[ch];
        const published = c?.state === "published";
        const label = CHANNEL_LABELS[ch];
        const clickable = published && !!c?.url;
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
        return (
          <span
            key={ch}
            title={`${label} ${published ? "published" : "not published"}`}
          >
            {published && c?.url ? (
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

export function RecipeDrawer({
  recipe,
  loading,
  onClose,
}: {
  recipe: RecipeDetail | null;
  loading: boolean;
  onClose: () => void;
}) {
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
            <div className="flex items-center gap-3 mt-1 mb-4 text-sm text-slate-500">
              <SafetyBadge safe={recipe.dog_safe === true} />
              {recipe.category && <span>{recipe.category}</span>}
            </div>

            <h3 className="font-medium text-slate-700 mb-1">Publishing</h3>
            <div className="mb-2">
              <PublishBadges status={recipe.publish_status} />
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
    </div>
  );
}
