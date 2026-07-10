import { endpoints } from "../api/endpoints";
import type { BrandsResponse } from "../api/brands";
import { useApiQuery } from "../hooks/useApiQuery";
import BrandsList from "../components/BrandsList";
import BrandForm from "../components/BrandForm";

/**
 * Onboarding — brand registry + provisioning. Two sections: the existing
 * `brands` table (read via `GET /brands`, same `useApiQuery` + table pattern
 * as `pages/Published.tsx`) and the create form (`POST /brands`). A
 * successful create or reprovision refetches the shared list below.
 */
export default function Onboarding(): React.JSX.Element {
  const { data, loading, error, refetch } = useApiQuery<BrandsResponse>(endpoints.brands());

  return (
    <div className="px-8 py-6 space-y-8">
      <header className="mb-2">
        <h1 className="font-display text-2xl font-semibold text-slate-800">Brand Onboarding</h1>
        <p className="text-sm text-slate-500">
          Register a new brand and provision its <code className="font-mono text-xs">brands/&lt;slug&gt;/</code>{" "}
          folder, config, and scanner schedule.
        </p>
      </header>

      <BrandsList
        brands={data?.brands ?? []}
        loading={loading}
        error={error}
        onChanged={() => void refetch()}
      />

      <BrandForm onCreated={() => void refetch()} />
    </div>
  );
}
