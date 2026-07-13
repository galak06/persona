import { useState } from "react";

export default function CodeBlock({ code }: { code: string }): React.JSX.Element {
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
