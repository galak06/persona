import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { endpoints } from "../api/endpoints";
import type { Brand, BrandCreateResponse, BrandSettingsRequest } from "../api/brands";
import { useApiQuery } from "../hooks/useApiQuery";
import { useApiMutation } from "../hooks/useApiMutation";
import { useToast } from "../components/ui/Toast";
import Alert from "../components/ui/Alert";
import FlowReadinessPanel from "../components/FlowReadinessPanel";

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
}

const FB_GROUP_SCOUT = "fb-group-scout";

function parseList(value: string): string[] {
  return value
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

function formStateFromBrand(brand: Brand): FormState {
  return {
    headless: brand.headless,
    primary_keywords: brand.keywords.primary_keywords.join(", "),
    secondary_keywords: brand.keywords.secondary_keywords.join(", "),
    competitor_mentions: brand.keywords.competitor_mentions.join(", "),
    competitor_accounts: brand.competitor_accounts.join(", "),
    enabled_flows: brand.enabled_flows,
    group_join_limit: String(brand.group_join_limit),
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
  const { data: brand, loading, error } = useApiQuery<Brand>(id ? endpoints.brand(id) : null);
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
    };

    const updated = await mutate(endpoints.brandSettings(id), payload);
    if (updated) {
      setResult(updated);
      toast.success(`Settings saved for ${updated.name}`, updated.brand_dir);
    } else {
      toast.error(`Could not save settings for ${id}`);
    }
  };

  return (
    <div className="px-8 py-6 space-y-6">
      <header className="mb-2">
        <Link to="/onboarding" className="text-xs text-amber-700 hover:underline">
          ← Back to Onboarding
        </Link>
        <h1 className="font-display text-2xl font-semibold text-slate-800 mt-1">
          Brand Settings{brand ? ` — ${brand.name}` : ""}
        </h1>
        <p className="text-sm text-slate-500">
          Edits take effect on the next scanner run — saving re-provisions{" "}
          <code className="font-mono text-xs">brand.json</code>,{" "}
          <code className="font-mono text-xs">config.json</code>, and{" "}
          <code className="font-mono text-xs">instagram_accounts.csv</code>.
        </p>
      </header>

      <FlowReadinessPanel brandId={id} />

      {loading && !form && <p className="text-sm text-slate-400">Loading…</p>}
      {error && <Alert status="error">Could not load brand: {error}</Alert>}

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
