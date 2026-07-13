import { useState } from "react";
import { Link } from "react-router-dom";
import type { BrandCreateResponse } from "../api/brands";
import Alert from "./ui/Alert";

/**
 * Post-create/re-provision success panel — split out of `BrandForm.tsx` to
 * keep that file under the project's 300-line limit.
 *
 * Beyond listing what provisioning wrote, this is the "what happens next"
 * moment for a first-time operator: it links straight to the flow-readiness
 * settings page (where login status, last-run, and readiness signals live)
 * instead of leaving them to notice the small "Settings" link back on the
 * brands list, and flags that `fb-group-scout` is off by default so a
 * silent, empty `fb-scanner` doesn't read as broken.
 */

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

export default function BrandCreateResult({
  result,
}: {
  result: BrandCreateResponse;
}): React.JSX.Element {
  return (
    <div className="mt-4 rounded-xl border border-emerald-200 bg-emerald-50/60 p-4 space-y-3">
      <p className="text-sm font-semibold text-emerald-800">
        Brand created — <span className="font-mono">{result.brand_dir}</span>
      </p>

      <div className="rounded-lg border border-emerald-300 bg-white p-3">
        <p className="text-sm font-semibold text-slate-700 mb-1">What's next</p>
        <ol className="text-sm text-slate-600 list-decimal list-inside space-y-1">
          <li>Log in below (once per platform) — this is what scanning and commenting actually run on.</li>
          <li>
            Open{" "}
            <Link
              to={`/onboarding/${result.id}/settings`}
              className="font-medium text-amber-700 hover:underline"
            >
              flow status
            </Link>{" "}
            to see when each flow is ready to run, and to Run Now on demand.
          </li>
          <li className="text-slate-400">
            Optional —{" "}
            <Link
              to={`/onboarding/${result.id}/connect`}
              className="font-medium text-amber-700 hover:underline"
            >
              connect Facebook &amp; Instagram
            </Link>{" "}
            only if you later want to publish Page/IG feed posts via the API. Not needed for
            scanning or commenting.
          </li>
        </ol>
      </div>

      <Alert status="info">
        <code className="font-mono text-xs">fb-group-scout</code> (finds new Facebook groups to
        join) is off by default — enable it on the flow status page above once you're ready;
        until then <code className="font-mono text-xs">fb-scanner</code> will find nothing to
        scan.
      </Alert>

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
