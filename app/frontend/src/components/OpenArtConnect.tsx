import { useEffect, useState } from "react";
import { fetchOpenartStatus, type OpenArtState } from "../api/oauth";

const HINT =
  "Optional: connect OpenArt to generate AI images for each beat instead of reusing the post's hero image";

/** `null` = pre-flight still in flight, "unknown" = the pre-flight itself failed. */
type Resolved = OpenArtState | "unknown" | null;

interface Props {
  onAuthorize: () => void;
}

/**
 * The OpenArt connect affordance, driven by live connection state rather than
 * rendered unconditionally.
 *
 * An always-visible "Connect OpenArt" link misreports state: sitting next to
 * the callback's "OpenArt connected" banner it told the operator they were
 * both connected and not connected, and reading it as live status they
 * concluded the token was being lost on every refresh. So each state gets
 * exactly one affordance:
 *
 *   ok              quiet indicator, no call to action -- nothing to do
 *   missing         a real, actionable button -- authorizing is the next step
 *   not_configured  nothing: `/start` 503s for this brand, so offering it lies
 *   null            nothing yet; avoids flashing a control we may hide
 *   unknown         fail open to the old understated link -- a broken status
 *                   check must never hide a control the operator might need
 */
export default function OpenArtConnect({ onAuthorize }: Props): React.JSX.Element | null {
  const [state, setState] = useState<Resolved>(null);

  useEffect(() => {
    void fetchOpenartStatus()
      .then((status) => setState(status.state))
      .catch(() => setState("unknown"));
  }, []);

  if (state === null || state === "not_configured") return null;

  if (state === "ok") {
    return (
      <span
        className="text-xs text-slate-400"
        title="Reel beats are generated as AI images. Expiring access tokens refresh silently."
      >
        ✓ OpenArt connected
      </span>
    );
  }

  return (
    <button
      type="button"
      onClick={onAuthorize}
      title={HINT}
      className={
        state === "missing"
          ? "px-3 py-1.5 rounded-lg border border-indigo-200 bg-indigo-50 text-xs font-semibold text-indigo-700 hover:bg-indigo-100"
          : "text-xs text-slate-400 underline underline-offset-2 hover:text-slate-600"
      }
    >
      Connect OpenArt
    </button>
  );
}
