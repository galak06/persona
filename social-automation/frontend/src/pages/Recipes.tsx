import { useEffect, useMemo, useState } from "react";
import {
  fetchRecipe,
  fetchRecipes,
  syncPublishStatus,
  type PublishChannel,
  type RecipeDetail,
  type RecipeSummary,
} from "../api/recipes";
import { getErrorMessage } from "../api/client";
import {
  IgModal,
  PublishBadges,
  RecipeDrawer,
  SafetyBadge,
} from "./RecipeDrawer";

type SafetyFilter = "all" | "safe" | "flagged";

const SAFETY_TABS: { id: SafetyFilter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "safe", label: "Dog-safe" },
  { id: "flagged", label: "Flagged" },
];

export default function Recipes() {
  const [recipes, setRecipes] = useState<RecipeSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [safety, setSafety] = useState<SafetyFilter>("all");
  const [selected, setSelected] = useState<RecipeDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [syncMsg, setSyncMsg] = useState("");
  const [igModal, setIgModal] = useState<PublishChannel | null>(null);

  useEffect(() => {
    void load();
  }, []);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchRecipes();
      setRecipes(res.recipes);
    } catch (err) {
      setError(getErrorMessage(err, "Failed to load recipes"));
    } finally {
      setLoading(false);
    }
  }

  async function handleSync() {
    setSyncing(true);
    setSyncMsg("");
    try {
      const res = await syncPublishStatus();
      setSyncMsg(`Synced ${res.updated} of ${res.total} from publish records`);
      await load();
    } catch (err) {
      setSyncMsg(getErrorMessage(err, "Sync failed"));
    } finally {
      setSyncing(false);
    }
  }

  async function openDetail(id: string) {
    setDetailLoading(true);
    try {
      setSelected(await fetchRecipe(id));
    } catch (err) {
      alert(getErrorMessage(err, "Failed to load recipe"));
    } finally {
      setDetailLoading(false);
    }
  }

  const counts = useMemo(() => {
    const safe = recipes.filter((r) => r.dog_safe === true).length;
    const published = recipes.filter((r) => !!r.wp_url).length;
    return {
      total: recipes.length,
      safe,
      flagged: recipes.length - safe,
      published,
    };
  }, [recipes]);

  const filtered = useMemo(() => {
    return recipes.filter((r) => {
      if (safety === "safe" && r.dog_safe !== true) return false;
      if (safety === "flagged" && r.dog_safe === true) return false;
      return true;
    });
  }, [recipes, safety]);

  if (loading) {
    return <div className="text-slate-500">Loading recipes...</div>;
  }

  if (error) {
    return (
      <div className="bg-red-50 text-red-700 p-4 rounded-md">
        <h3 className="font-semibold mb-1">Error loading recipes</h3>
        <p className="text-sm">{error}</p>
        <button
          onClick={load}
          className="mt-3 text-sm font-medium hover:underline"
        >
          Try Again
        </button>
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-baseline justify-between mb-4">
        <h1 className="text-xl font-semibold text-slate-800">Recipe DB</h1>
        <span className="text-sm text-slate-500">
          {counts.total} recipes ·{" "}
          <span className="text-green-700">{counts.published} published</span> ·{" "}
          {counts.safe} dog-safe · {counts.flagged} flagged
        </span>
      </div>

      <div className="flex items-center gap-3 mb-4">
        <div className="flex gap-1">
          {SAFETY_TABS.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setSafety(tab.id)}
              className={`px-4 py-1.5 rounded-full text-sm transition-colors ${
                safety === tab.id
                  ? "bg-cyan-50 text-cyan-700 font-medium"
                  : "text-slate-500 hover:text-slate-700"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-2">
          {syncMsg && <span className="text-xs text-slate-400">{syncMsg}</span>}
          <button
            onClick={() => void handleSync()}
            disabled={syncing}
            className="px-3 py-1.5 rounded-md text-sm bg-cyan-600 text-white hover:bg-cyan-700 disabled:opacity-50"
          >
            {syncing ? "Syncing…" : "Sync publish status"}
          </button>
        </div>
      </div>

      <div className="overflow-hidden rounded-md border border-slate-200">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-slate-500 text-left">
            <tr>
              <th className="px-4 py-2 font-medium">Recipe</th>
              <th className="px-4 py-2 font-medium">Safety</th>
              <th className="px-4 py-2 font-medium">Published</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {filtered.map((r) => (
              <tr
                key={r.id}
                onClick={() => void openDetail(r.id)}
                className={`cursor-pointer border-l-2 ${
                  r.wp_url
                    ? "bg-green-50 hover:bg-green-100 border-green-400"
                    : "hover:bg-slate-50 border-transparent"
                }`}
              >
                <td className="px-4 py-2 text-slate-800">
                  {r.display_name || r.name}
                  {r.display_name && (
                    <span className="block text-xs text-slate-400">
                      source: {r.name}
                    </span>
                  )}
                </td>
                <td className="px-4 py-2">
                  <SafetyBadge safe={r.dog_safe === true} />
                  {r.toxic_flags && r.toxic_flags.length > 0 && (
                    <span className="ml-2 text-xs text-red-500">
                      {r.toxic_flags.join(", ")}
                    </span>
                  )}
                </td>
                <td className="px-4 py-2">
                  <PublishBadges
                    status={r.publish_status}
                    onIgClick={setIgModal}
                  />
                  {r.published_at && (
                    <span className="block text-xs text-slate-400 mt-0.5">
                      {r.published_at.slice(0, 10)}
                    </span>
                  )}
                </td>
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr>
                <td colSpan={3} className="px-4 py-6 text-center text-slate-400">
                  No recipes match these filters.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {(selected || detailLoading) && (
        <RecipeDrawer
          recipe={selected}
          loading={detailLoading}
          onClose={() => setSelected(null)}
        />
      )}

      {igModal && (
        <IgModal ig={igModal} onClose={() => setIgModal(null)} />
      )}
    </div>
  );
}
