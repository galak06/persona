import { useState } from "react";
import type { FormEvent } from "react";
import { endpoints } from "../api/endpoints";
import type { BrandCreateRequest, BrandCreateResponse } from "../api/brands";
import { useApiMutation } from "../hooks/useApiMutation";
import Alert from "./ui/Alert";
import BrandCreateResult from "./BrandCreateResult";
import {
  EMPTY_FORM,
  FIELD_SECTIONS,
  LIST_FIELDS,
  URL_FIELD_KEYS,
  isValidUrl,
  parseList,
} from "./brandFormFields";
import type { FormState } from "./brandFormFields";

/**
 * Brand-creation form — the other half of the Onboarding page. Covers every
 * `BrandCreateRequest` field (mirrors `BrandSpec` in
 * `persona/lib/brand_templates.py`): plain text inputs for the simple
 * fields, comma-separated text parsed to `string[]` for the 4 list fields
 * (no dedicated tag-input component exists yet in `components/ui/`), and a
 * free-text area for `brand_persona` — the only brand-facts field
 * `BrandSpec` currently accepts; a blank value renders as an explicit
 * `<!-- TODO (owner) -->` placeholder server-side rather than an invented
 * one.
 *
 * Fields are grouped into labeled sections (Identity / Social profiles /
 * Brand voice / Keywords) rather than one flat grid — each section explains
 * in one line why it matters, since a first-time operator has no other
 * source of that context. Field config + validation live in
 * `brandFormFields.ts` (split out to keep this file under the project's
 * 300-line limit).
 */

interface BrandFormProps {
  onCreated: () => void;
}

export default function BrandForm({ onCreated }: BrandFormProps): React.JSX.Element {
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [result, setResult] = useState<BrandCreateResponse | null>(null);
  const { mutate, loading, error, errorStatus, errorDetail } = useApiMutation<
    BrandCreateResponse,
    BrandCreateRequest
  >("post");
  const retry = useApiMutation<BrandCreateResponse, undefined>("post");

  const update =
    (key: keyof FormState) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
      setForm((f) => ({ ...f, [key]: e.target.value }));
    };

  const urlErrors: Partial<Record<keyof FormState, string>> = {};
  for (const key of URL_FIELD_KEYS) {
    if (!isValidUrl(form[key])) {
      urlErrors[key] = "Enter a full URL, starting with https://";
    }
  }

  const canSubmit =
    form.name.trim() !== "" &&
    form.site_url.trim() !== "" &&
    form.niche.trim() !== "" &&
    Object.keys(urlErrors).length === 0 &&
    !loading;

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;

    const payload: BrandCreateRequest = {
      name: form.name.trim(),
      site_url: form.site_url.trim(),
      niche: form.niche.trim(),
      target_audience: form.target_audience.trim(),
      mascot_name: form.mascot_name.trim(),
      brand_persona: form.brand_persona.trim(),
      instagram_profile_url: form.instagram_profile_url.trim(),
      facebook_page_url: form.facebook_page_url.trim(),
      primary_keywords: parseList(form.primary_keywords),
      secondary_keywords: parseList(form.secondary_keywords),
      competitor_mentions: parseList(form.competitor_mentions),
      competitor_accounts: parseList(form.competitor_accounts),
    };

    const created = await mutate(endpoints.brands(), payload);
    if (created) {
      setResult(created);
      setForm(EMPTY_FORM);
      onCreated();
    }
  };

  // Provisioning failed after the brand row was already created (a 502 from
  // POST /brands) — the backend tells us exactly how to retry
  // (`detail.retry`/`detail.brand_id`); offer that directly instead of
  // making the operator re-fill and resubmit the whole form.
  const provisioningFailure =
    errorStatus === 502 && errorDetail && typeof errorDetail === "object"
      ? (errorDetail as { brand_id?: string })
      : null;

  const handleRetryProvisioning = async () => {
    if (!provisioningFailure?.brand_id) return;
    const retried = await retry.mutate(endpoints.brandProvision(provisioningFailure.brand_id));
    if (retried) {
      setResult(retried);
      onCreated();
    }
  };

  return (
    <section id="create-brand" className="scroll-mt-6">
      <h2 className="font-display text-lg font-semibold text-slate-800 mb-1">
        Onboard a new brand
      </h2>
      <p className="text-sm text-slate-500 mb-3">
        Creates the brand row, scaffolds <code className="font-mono text-xs">brands/&lt;slug&gt;/</code>, and
        seeds its <code className="font-mono text-xs">ig-scanner</code> /{" "}
        <code className="font-mono text-xs">fb-scanner</code> schedule.
      </p>

      <form
        onSubmit={(e) => void handleSubmit(e)}
        className="rounded-xl border border-stone-200 bg-white p-5 space-y-6"
      >
        {FIELD_SECTIONS.map((section) => (
          <div key={section.title}>
            <h3 className="text-sm font-semibold text-slate-700">{section.title}</h3>
            <p className="text-xs text-slate-400 mb-2">{section.description}</p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              {section.fields.map((field) => (
                <label key={field.key} className="text-sm">
                  <span className="block mb-1 font-medium text-slate-700">
                    {field.label}
                    {field.required && <span className="text-rose-500"> *</span>}
                  </span>
                  <input
                    type="text"
                    value={form[field.key]}
                    onChange={update(field.key)}
                    required={field.required}
                    placeholder={field.placeholder}
                    aria-invalid={Boolean(urlErrors[field.key])}
                    className={`w-full rounded-lg border px-3 py-2 text-sm focus:ring ${
                      urlErrors[field.key]
                        ? "border-rose-300 focus:border-rose-400 focus:ring-rose-200/50"
                        : "border-stone-300 focus:border-amber-300 focus:ring-amber-200/50"
                    }`}
                  />
                  {urlErrors[field.key] && (
                    <span className="mt-1 block text-xs text-rose-600">
                      {urlErrors[field.key]}
                    </span>
                  )}
                </label>
              ))}
            </div>
          </div>
        ))}

        <div>
          <h3 className="text-sm font-semibold text-slate-700">Brand voice</h3>
          <p className="text-xs text-slate-400 mb-2">
            Grounds engagement comments in a real voice — leave blank if you haven't decided yet.
          </p>
          <textarea
            value={form.brand_persona}
            onChange={update("brand_persona")}
            rows={3}
            placeholder="e.g. a dog owner and software engineer, not a vet"
            className="w-full rounded-lg border border-stone-300 px-3 py-2 text-sm focus:border-amber-300 focus:ring focus:ring-amber-200/50"
          />
        </div>

        <div>
          <h3 className="text-sm font-semibold text-slate-700">Keywords & competitors</h3>
          <p className="text-xs text-slate-400 mb-2">
            Drive ig-scanner's hashtag list and relevance scoring — leaving these blank means
            scanners start with nothing to look for.
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {LIST_FIELDS.map((field) => (
              <label key={field.key} className="text-sm">
                <span className="block mb-1 font-medium text-slate-700">
                  {field.label} <span className="font-normal text-slate-400">(comma-separated)</span>
                </span>
                <input
                  type="text"
                  value={form[field.key]}
                  onChange={update(field.key)}
                  placeholder="e.g. grain-free, raw diet, joint health"
                  className="w-full rounded-lg border border-stone-300 px-3 py-2 text-sm focus:border-amber-300 focus:ring focus:ring-amber-200/50"
                />
              </label>
            ))}
          </div>
        </div>

        {error && (
          <Alert status="error">
            {errorStatus === 409
              ? "A brand with this name already exists — choose a different name."
              : error}
            {provisioningFailure?.brand_id && (
              <div className="mt-2">
                <button
                  type="button"
                  onClick={() => void handleRetryProvisioning()}
                  disabled={retry.loading}
                  className="rounded-lg border border-rose-300 bg-white px-3 py-1.5 text-xs font-semibold text-rose-700 hover:bg-rose-50 disabled:opacity-50"
                >
                  {retry.loading ? "Retrying…" : "Retry provisioning"}
                </button>
              </div>
            )}
          </Alert>
        )}

        <button
          type="submit"
          disabled={!canSubmit}
          className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-semibold text-white hover:bg-amber-700 disabled:opacity-50"
        >
          {loading ? "Provisioning…" : "Create brand"}
        </button>
      </form>

      {result && <BrandCreateResult result={result} />}
    </section>
  );
}
