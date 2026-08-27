import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { endpoints } from "../api/endpoints";
import type {
  Brand,
  BrandCreateResponse,
  BrandIdeaCategories,
  BrandKeywords,
  BrandSettingsRequest,
} from "../api/brands";
import { useApiQuery } from "../hooks/useApiQuery";
import { useApiMutation } from "../hooks/useApiMutation";
import { useToast } from "../components/ui/Toast";
import Alert from "../components/ui/Alert";
import ErrorState from "../components/ui/ErrorState";
import LoadingState from "../components/ui/LoadingState";
import ReferenceLibrarySection from "../components/references/ReferenceLibrarySection";

/**
 * Brand settings — edit an already-provisioned brand's headless mode and
 * search keyword/competitor lists (`PATCH /brands/{id}/settings`). Reached
 * from a "Settings" link on each `BrandsList` row.
 *
 * The submit always re-sends every field currently in the form (not a
 * diff) -- the PATCH endpoint is idempotent per-field either way, and a
 * full-resend keeps this page's state model simple (one `FormState`, no
 * separate dirty-tracking).
 */

interface FormState {
  headless: boolean;
  primary_keywords: string;
  secondary_keywords: string;
  competitor_mentions: string;
  competitor_accounts: string;
  enabled_flows: string[];
  group_join_limit: string;
  focus_category: string;
}

const FB_GROUP_SCOUT = "fb-group-scout";

/** Mirrors `lib.content_strategy.normalize_category`: trim, collapse inner
 * whitespace, casefold. Nothing else -- "Dog Food" and "Dog Foods" are
 * genuinely different categories. */
function normalizeCat(value: string): string {
  return value.trim().replace(/\s+/g, " ").toLowerCase();
}

function parseList(value: string): string[] {
  return value
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

/** Render one list field, tolerating a list the response didn't carry. */
function toCsv(values: readonly string[] | undefined): string {
  return values ? values.join(", ") : "";
}

/**
 * `brand` is an unchecked CAST of network JSON (`useApiQuery<Brand>`), not a
 * validated value — so a list the schema marks required can still arrive
 * missing from an API one deploy behind, and reading it unguarded throws
 * during render and takes the whole page down. That is precisely how this
 * function crashed: the backend used to hand `keywords` straight through as
 * an untyped dict, and a brand onboarded without keywords stores `{}`, so
 * `keywords.primary_keywords.join(", ")` hit `undefined`. The schema is
 * honest now (every category is required and always emitted), and every list
 * read here still goes through `toCsv`, so a missing one degrades to an empty
 * field instead of a blank page.
 */
function formStateFromBrand(brand: Brand): FormState {
  const keywords: Partial<BrandKeywords> = brand.keywords ?? {};
  return {
    headless: brand.headless,
    primary_keywords: toCsv(keywords.primary_keywords),
    secondary_keywords: toCsv(keywords.secondary_keywords),
    competitor_mentions: toCsv(keywords.competitor_mentions),
    competitor_accounts: toCsv(brand.competitor_accounts),
    enabled_flows: brand.enabled_flows ?? [],
    group_join_limit: String(brand.group_join_limit),
    focus_category: brand.focus_category ?? "",
  };
}

const LIST_FIELDS: {
  key: "primary_keywords" | "secondary_keywords" | "competitor_mentions" | "competitor_accounts";
  label: string;
}[] = [
  { key: "primary_keywords", label: "Primary keywords" },
  { key: "secondary_keywords", label: "Secondary keywords" },
  { key: "competitor_mentions", label: "Competitor mentions" },
  { key: "competitor_accounts", label: "Competitor accounts" },
];

export default function BrandSettings(): React.JSX.Element {
  const { id } = useParams<{ id: string }>();
  const {
    data: brand,
    loading,
    error,
    refetch,
  } = useApiQuery<Brand>(id ? endpoints.brand(id) : null);
  // Best-effort: a brand with no ideas yet simply gets no suggestions.
  const { data: ideaCategories } = useApiQuery<BrandIdeaCategories>(
    id ? endpoints.brandIdeaCategories(id) : null,
  );
  const { toast } = useToast();
  const { mutate, loading: saving, error: saveError } = useApiMutation<
    BrandCreateResponse,
    BrandSettingsRequest
  >("patch");

  const [form, setForm] = useState<FormState | null>(null);
  const [result, setResult] = useState<BrandCreateResponse | null>(null);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (brand) setForm(formStateFromBrand(brand));
  }, [brand]);

  if (!id) return <Alert status="error">No brand id in URL.</Alert>;

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!form) return;

    const parsedLimit = Number.parseInt(form.group_join_limit, 10);
    const payload: BrandSettingsRequest = {
      headless: form.headless,
      primary_keywords: parseList(form.primary_keywords),
      secondary_keywords: parseList(form.secondary_keywords),
      competitor_mentions: parseList(form.competitor_mentions),
      competitor_accounts: parseList(form.competitor_accounts),
      enabled_flows: form.enabled_flows,
      group_join_limit: Number.isNaN(parsedLimit) ? undefined : parsedLimit,
      // "" is meaningful here (clear the focus), so it is always sent --
      // only `undefined` means "leave alone" on the PATCH side.
      focus_category: form.focus_category.trim(),
    };

    const updated = await mutate(endpoints.brandSettings(id), payload);
    if (updated) {
      setResult(updated);
      toast.success(`Settings saved for ${updated.name}`, updated.brand_dir);
    } else {
      toast.error(`Could not save settings for ${id}`);
    }
  };

  const knownCategories = ideaCategories?.categories ?? [];
  const siteCategories = ideaCategories?.site_categories ?? [];
  // Warn (never block) when the typed focus matches nothing this brand's ideas
  // have used: the gate compares on exactly this string, so a typo here
  // rejects every new idea instead of failing loudly. Matching is case- and
  // whitespace-insensitive, same as `lib.content_strategy.normalize_category`.
  const typedFocus = form?.focus_category.trim() ?? "";
  // The check that actually protects the SEO effect: internal-link ranking
  // compares the focus against a post's real WordPress categories, so a focus
  // no published post uses leaves the clustering doing nothing, silently.
  const focusNotOnSite =
    typedFocus !== "" &&
    siteCategories.length > 0 &&
    !siteCategories.some((c) => normalizeCat(c) === normalizeCat(typedFocus));

  const focusIsUnknown =
    typedFocus !== "" &&
    knownCategories.length > 0 &&
    !knownCategories.some((c) => normalizeCat(c) === normalizeCat(typedFocus));

  return (
    <div className="px-8 py-6 space-y-6">
      <header className="mb-2">
        <Link to="/onboarding" className="text-xs text-amber-700 hover:underline">
          ← Back to Onboarding
        </Link>
        <div className="flex items-center justify-between gap-3 flex-wrap mt-1">
          <h1 className="font-display text-2xl font-semibold text-slate-800">
            Brand Settings{brand ? ` — ${brand.name}` : ""}
          </h1>
          <Link
            to={`/onboarding/${id}/connect`}
            className="text-xs text-slate-400 hover:text-amber-700 hover:underline whitespace-nowrap"
          >
            Connect Facebook &amp; Instagram (optional) →
          </Link>
        </div>
        <p className="text-sm text-slate-500">
          Edits take effect on the next scanner run — saving re-provisions{" "}
          <code className="font-mono text-xs">brand.json</code>,{" "}
          <code className="font-mono text-xs">config.json</code>, and{" "}
          <code className="font-mono text-xs">instagram_accounts.csv</code>.
        </p>
      </header>

      <Alert status="info">
        Flow status and Run Now moved to{" "}
        <Link to="/human-mimic" className="font-medium underline">
          Human Mimic
        </Link>
        .
      </Alert>

      {loading && !form && <LoadingState message="Loading brand…" />}
      {error && (
        <ErrorState
          message={`Could not load brand: ${error}`}
          onRetry={() => void refetch()}
          retrying={loading}
        />
      )}

      {form && (
        <form
          onSubmit={(e) => void handleSubmit(e)}
          className="rounded-xl border border-stone-200 bg-white p-5 space-y-4"
        >
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={!form.headless}
              onChange={(e) => setForm({ ...form, headless: !e.target.checked })}
              className="h-4 w-4 rounded border-stone-300 text-amber-600 focus:ring-amber-300"
            />
            <span className="font-medium text-slate-700">Show browser window (disable headless)</span>
          </label>
          <p className="text-xs text-slate-400 -mt-2">
            Off by default (headless) — production-safe. Turn on for local debugging to watch the
            scanner's browser live.
          </p>

          <div className="border-t border-stone-100 pt-4 space-y-3">
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={form.enabled_flows.includes(FB_GROUP_SCOUT)}
                onChange={(e) =>
                  setForm({
                    ...form,
                    enabled_flows: e.target.checked
                      ? [...form.enabled_flows, FB_GROUP_SCOUT]
                      : form.enabled_flows.filter((f) => f !== FB_GROUP_SCOUT),
                  })
                }
                className="h-4 w-4 rounded border-stone-300 text-amber-600 focus:ring-amber-300"
              />
              <span className="font-medium text-slate-700">
                Enable fb-group-scout (find new Facebook groups to join)
              </span>
            </label>

            <label className="block text-sm max-w-xs">
              <span className="block mb-1 font-medium text-slate-700">
                Daily group-join limit
              </span>
              <input
                type="number"
                min={0}
                value={form.group_join_limit}
                onChange={(e) => setForm({ ...form, group_join_limit: e.target.value })}
                disabled={!form.enabled_flows.includes(FB_GROUP_SCOUT)}
                className="w-full rounded-lg border border-stone-300 px-3 py-2 text-sm focus:border-amber-300 focus:ring focus:ring-amber-200/50 disabled:bg-stone-50 disabled:text-slate-400"
              />
            </label>
          </div>

          <label className="block text-sm max-w-md">
            <span className="block mb-1 font-medium text-slate-700">
              Focus category <span className="font-normal text-slate-400">(optional)</span>
            </span>
            <input
              type="text"
              list="focus-category-options"
              placeholder="No focus — ideas may come from any category"
              value={form.focus_category}
              onChange={(e) => setForm({ ...form, focus_category: e.target.value })}
              className="w-full rounded-lg border border-stone-300 px-3 py-2 text-sm focus:border-amber-300 focus:ring focus:ring-amber-200/50"
            />
            <datalist id="focus-category-options">
              {knownCategories.map((c) => (
                <option key={c} value={c} />
              ))}
            </datalist>
            <p className="mt-1 text-xs text-slate-500">
              Restricts idea generation to a single category, and tells the idea agent to go
              deeper rather than broader. Leave blank for no focus.
            </p>
            {focusNotOnSite && (
              <p className="mt-1 text-xs text-amber-700">
                No published post uses “{typedFocus}” as a WordPress category, so internal-link
                clustering will have no effect. Site categories: {siteCategories.join(", ")}
              </p>
            )}
            {focusIsUnknown && (
              <p className="mt-1 text-xs text-amber-700">
                No idea has used “{form.focus_category.trim()}” yet. If that is a typo, every new
                idea will be rejected. Known: {knownCategories.join(", ")}
              </p>
            )}
          </label>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {LIST_FIELDS.map((field) => (
              <label key={field.key} className="text-sm">
                <span className="block mb-1 font-medium text-slate-700">
                  {field.label} <span className="font-normal text-slate-400">(comma-separated)</span>
                </span>
                <input
                  type="text"
                  value={form[field.key]}
                  onChange={(e) => setForm({ ...form, [field.key]: e.target.value })}
                  className="w-full rounded-lg border border-stone-300 px-3 py-2 text-sm focus:border-amber-300 focus:ring focus:ring-amber-200/50"
                />
              </label>
            ))}
          </div>

          {saveError && <Alert status="error">{saveError}</Alert>}

          <button
            type="submit"
            disabled={saving}
            className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-semibold text-white hover:bg-amber-700 disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save settings"}
          </button>
        </form>
      )}

      <ReferenceLibrarySection brandId={id} />

      {result && (
        <div className="rounded-xl border border-emerald-200 bg-emerald-50/60 p-4 space-y-2">
          <p className="text-sm font-semibold text-emerald-800">
            Re-provisioned — <span className="font-mono">{result.brand_dir}</span>
          </p>
          {result.warnings.length > 0 &&
            result.warnings.map((w) => (
              <Alert key={w} status="warning">
                {w}
              </Alert>
            ))}
        </div>
      )}
    </div>
  );
}
