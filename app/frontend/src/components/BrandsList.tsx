import { Link } from "react-router-dom";
import { endpoints } from "../api/endpoints";
import type { BrandCreateResponse, BrandSummary } from "../api/brands";
import { useApiMutation } from "../hooks/useApiMutation";
import { useToast } from "./ui/Toast";
import EmptyState from "./ui/EmptyState";
import ErrorState from "./ui/ErrorState";
import LoadingState from "./ui/LoadingState";

/**
 * Existing-brands table half of the Onboarding page. Read-only list +
 * a "Reprovision" retry action (`POST /brands/{id}/provision`, idempotent —
 * re-renders config.json/brand_facts.md/instagram_accounts.csv and
 * re-upserts the 2 schedule_tasks rows without touching anything else).
 *
 * Data is fetched by the parent (`Onboarding.tsx`, same `useApiQuery` +
 * table pattern as `pages/Published.tsx`) so a successful create in
 * `BrandForm` can trigger one shared refetch.
 */

interface BrandsListProps {
  brands: BrandSummary[];
  loading: boolean;
  error: string;
  onChanged: () => void;
}

function statusClasses(status: string): string {
  if (status === "active" || status === "provisioned") {
    return "bg-emerald-50 text-emerald-700";
  }
  if (status === "disabled") return "bg-rose-50 text-rose-700";
  return "bg-amber-50 text-amber-700"; // draft | provisioning
}

function ReprovisionButton({ id, onDone }: { id: string; onDone: () => void }): React.JSX.Element {
  const { toast } = useToast();
  const { mutate, loading } = useApiMutation<BrandCreateResponse, undefined>("post");

  const handleClick = async () => {
    const result = await mutate(endpoints.brandProvision(id));
    if (result) {
      toast.success(`Reprovisioned ${id}`, result.brand_dir);
      onDone();
    } else {
      toast.error(`Reprovision failed for ${id}`);
    }
  };

  return (
    <button
      type="button"
      onClick={() => void handleClick()}
      disabled={loading}
      className="rounded-lg border border-stone-200 bg-white px-2.5 py-1 text-xs font-medium text-slate-600 hover:bg-stone-50 disabled:opacity-50"
    >
      {loading ? "Reprovisioning…" : "Reprovision"}
    </button>
  );
}

function Row({ brand, onChanged }: { brand: BrandSummary; onChanged: () => void }): React.JSX.Element {
  return (
    <tr className="border-b border-stone-100 hover:bg-stone-50/60 align-top">
      <td className="py-2 pr-3 text-sm font-medium text-slate-800">{brand.name}</td>
      <td className="py-2 pr-3 text-sm text-slate-600">{brand.niche || "—"}</td>
      <td className="py-2 pr-3 whitespace-nowrap">
        <span className={`rounded px-2 py-0.5 text-xs font-medium capitalize ${statusClasses(brand.status)}`}>
          {brand.status}
        </span>
      </td>
      <td className="py-2 pr-3 text-xs text-slate-500">
        {brand.enabled_flows.length > 0 ? brand.enabled_flows.join(", ") : "—"}
      </td>
      <td className="py-2 pr-3 text-xs font-mono text-slate-400 max-w-xs truncate" title={brand.brand_dir}>
        {brand.brand_dir || "—"}
      </td>
      <td className="py-2 pr-3 whitespace-nowrap text-xs text-slate-400 tabular-nums">
        {brand.created_at ? brand.created_at.slice(0, 10) : "—"}
      </td>
      <td className="py-2 whitespace-nowrap space-x-2">
        <Link
          to={`/onboarding/${brand.id}/settings`}
          className="rounded-lg border border-stone-200 bg-white px-2.5 py-1 text-xs font-medium text-slate-600 hover:bg-stone-50 inline-block"
        >
          Settings
        </Link>
        <ReprovisionButton id={brand.id} onDone={onChanged} />
      </td>
    </tr>
  );
}

export default function BrandsList({ brands, loading, error, onChanged }: BrandsListProps): React.JSX.Element {
  return (
    <section>
      <h2 className="font-display text-lg font-semibold text-slate-800 mb-1">Brands</h2>
      <p className="text-sm text-slate-500 mb-3">
        Onboarded brands and their provisioning status — {brands.length} total.
      </p>

      {loading && brands.length === 0 && <LoadingState message="Loading brands…" />}
      {error && (
        <ErrorState
          message={`Could not load brands: ${error}`}
          onRetry={onChanged}
          retrying={loading}
        />
      )}

      {!loading && !error && brands.length === 0 && (
        <EmptyState
          title="No brands onboarded yet"
          description="Create your first brand below to provision its folder, config, and scanner schedule."
          actions={
            <a
              href="#create-brand"
              className="inline-block rounded-lg bg-amber-600 px-4 py-2 text-sm font-semibold text-white hover:bg-amber-700"
            >
              Create your first brand ↓
            </a>
          }
        />
      )}

      {brands.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-stone-200 bg-white">
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-stone-200 text-xs uppercase tracking-wide text-slate-400">
                <th className="py-2 px-3 font-medium">Name</th>
                <th className="py-2 pr-3 font-medium">Niche</th>
                <th className="py-2 pr-3 font-medium">Status</th>
                <th className="py-2 pr-3 font-medium">Enabled flows</th>
                <th className="py-2 pr-3 font-medium">Brand dir</th>
                <th className="py-2 pr-3 font-medium">Created</th>
                <th className="py-2 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody className="[&_td:first-child]:pl-3">
              {brands.map((b) => (
                <Row key={b.id} brand={b} onChanged={onChanged} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
