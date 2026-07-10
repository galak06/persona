import { useState } from "react";
import type { FormEvent } from "react";
import { endpoints } from "../api/endpoints";
import type { BrandCreateRequest, BrandCreateResponse } from "../api/brands";
import { useApiMutation } from "../hooks/useApiMutation";
import Alert from "./ui/Alert";

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
 */

// All fields kept as strings while editing; the 4 list fields are split on
// submit. Keys match `BrandCreateRequest` so building the payload is a
// straight map, not a hand-maintained duplicate list.
type FormState = Record<keyof BrandCreateRequest, string>;

const EMPTY_FORM: FormState = {
  name: "",
  site_url: "",
  niche: "",
  target_audience: "",
  mascot_name: "",
  brand_persona: "",
  instagram_profile_url: "",
  facebook_page_url: "",
  primary_keywords: "",
  secondary_keywords: "",
  competitor_mentions: "",
  competitor_accounts: "",
};

interface TextFieldSpec {
  key: keyof FormState;
  label: string;
  required?: boolean;
  placeholder?: string;
}

const TEXT_FIELDS: TextFieldSpec[] = [
  { key: "name", label: "Name", required: true, placeholder: "Dog Food and Fun" },
  { key: "site_url", label: "Site URL", required: true, placeholder: "https://example.com" },
  { key: "niche", label: "Niche", required: true, placeholder: "Dog food & gear reviews" },
  { key: "target_audience", label: "Target audience" },
  { key: "mascot_name", label: "Mascot name" },
  { key: "instagram_profile_url", label: "Instagram profile URL" },
  { key: "facebook_page_url", label: "Facebook page URL" },
];

const LIST_FIELDS: { key: keyof FormState; label: string }[] = [
  { key: "primary_keywords", label: "Primary keywords" },
  { key: "secondary_keywords", label: "Secondary keywords" },
  { key: "competitor_mentions", label: "Competitor mentions" },
  { key: "competitor_accounts", label: "Competitor accounts" },
];

function parseList(value: string): string[] {
  return value
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

function CodeBlock({ code }: { code: string }): React.JSX.Element {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard
      .writeText(code)
      .then(() => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1500);
      })
      .catch(() => {
        // Clipboard API unavailable (e.g. insecure context) — no-op; the
        // command is still visible + selectable in the block below.
      });
  };

  return (
    <div className="relative">
      <pre className="rounded-lg bg-slate-900 text-slate-100 text-xs p-3 pr-16 overflow-x-auto">
        <code>{code}</code>
      </pre>
      <button
        type="button"
        onClick={handleCopy}
        className="absolute top-2 right-2 rounded bg-slate-700 px-2 py-1 text-[10px] font-semibold text-slate-100 hover:bg-slate-600"
      >
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

function ResultPanel({ result }: { result: BrandCreateResponse }): React.JSX.Element {
  return (
    <div className="mt-4 rounded-xl border border-emerald-200 bg-emerald-50/60 p-4 space-y-3">
      <p className="text-sm font-semibold text-emerald-800">
        Brand created — <span className="font-mono">{result.brand_dir}</span>
      </p>

      {result.files_written.length > 0 && (
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-emerald-700 mb-1">
            Files written
          </p>
          <ul className="text-xs font-mono text-slate-600 list-disc list-inside">
            {result.files_written.map((f) => (
              <li key={f}>{f}</li>
            ))}
          </ul>
        </div>
      )}

      {result.warnings.length > 0 && (
        <div className="space-y-1">
          {result.warnings.map((w) => (
            <Alert key={w} status="warning">
              {w}
            </Alert>
          ))}
        </div>
      )}

      <div>
        <p className="text-xs font-semibold uppercase tracking-wide text-emerald-700 mb-1">
          Instagram login (run once, before ig-scanner can do anything live)
        </p>
        <CodeBlock code={result.ig_login_command} />
      </div>

      <div>
        <p className="text-xs font-semibold uppercase tracking-wide text-emerald-700 mb-1">
          Facebook login (run once, before fb-scanner can do anything live)
        </p>
        <CodeBlock code={result.fb_login_command} />
      </div>
    </div>
  );
}

interface BrandFormProps {
  onCreated: () => void;
}

export default function BrandForm({ onCreated }: BrandFormProps): React.JSX.Element {
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [result, setResult] = useState<BrandCreateResponse | null>(null);
  const { mutate, loading, error, errorStatus } = useApiMutation<
    BrandCreateResponse,
    BrandCreateRequest
  >("post");

  const update = (key: keyof FormState) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    setForm((f) => ({ ...f, [key]: e.target.value }));
  };

  const canSubmit =
    form.name.trim() !== "" && form.site_url.trim() !== "" && form.niche.trim() !== "" && !loading;

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

  return (
    <section>
      <h2 className="font-display text-lg font-semibold text-slate-800 mb-1">Onboard a new brand</h2>
      <p className="text-sm text-slate-500 mb-3">
        Creates the brand row, scaffolds <code className="font-mono text-xs">brands/&lt;slug&gt;/</code>, and
        seeds its <code className="font-mono text-xs">ig-scanner</code> /{" "}
        <code className="font-mono text-xs">fb-scanner</code> schedule.
      </p>

      <form onSubmit={(e) => void handleSubmit(e)} className="rounded-xl border border-stone-200 bg-white p-5 space-y-4">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {TEXT_FIELDS.map((field) => (
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
                className="w-full rounded-lg border border-stone-300 px-3 py-2 text-sm focus:border-amber-300 focus:ring focus:ring-amber-200/50"
              />
            </label>
          ))}
        </div>

        <label className="block text-sm">
          <span className="block mb-1 font-medium text-slate-700">
            Brand persona / voice{" "}
            <span className="font-normal text-slate-400">(optional — leave blank if none yet)</span>
          </span>
          <textarea
            value={form.brand_persona}
            onChange={update("brand_persona")}
            rows={3}
            placeholder="e.g. a dog owner and software engineer, not a vet — grounds engagement comments in a real voice"
            className="w-full rounded-lg border border-stone-300 px-3 py-2 text-sm focus:border-amber-300 focus:ring focus:ring-amber-200/50"
          />
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
                onChange={update(field.key)}
                placeholder="e.g. grain-free, raw diet, joint health"
                className="w-full rounded-lg border border-stone-300 px-3 py-2 text-sm focus:border-amber-300 focus:ring focus:ring-amber-200/50"
              />
            </label>
          ))}
        </div>

        {error && (
          <Alert status="error">
            {errorStatus === 409
              ? "A brand with this name already exists — choose a different name."
              : error}
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

      {result && <ResultPanel result={result} />}
    </section>
  );
}
