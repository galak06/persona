/**
 * The read-only state pill on a spotlight card: one muted label for a row the
 * machinery owns, where the operator has no decision to make.
 *
 * `fb_publishing`/`ig_publishing` are the reason this exists. A worker killed
 * between claiming a row and writing its result leaves the claim behind, so a
 * row can sit in a publishing state indefinitely — and a spotlight that is
 * stuck has to be visibly stuck rather than missing from the page, which is
 * the one reading that must never happen.
 *
 * An unmapped status falls through to the de-snaked token, so a status added
 * server-side renders as itself instead of leaving a card with no state on it
 * at all. That fallback is the point of the `Record<string, …>` lookup over a
 * switch on the generated union: this must not need a frontend change to stay
 * readable.
 */

import type { SocialDerivative } from "../api/socialDerivatives";

const STATUS_PILLS: Record<string, string> = {
  fb_publishing: "Publishing to Facebook…",
  ig_publishing: "Publishing to Instagram…",
  fb_published: "Facebook posted — Instagram pending",
  published: "Published",
  rejected: "Rejected",
};

export interface SpotlightStatusPillProps {
  status: SocialDerivative["status"];
}

export default function SpotlightStatusPill({
  status,
}: SpotlightStatusPillProps): React.JSX.Element {
  const publishing = status === "fb_publishing" || status === "ig_publishing";
  return (
    <span
      title={
        publishing
          ? "Handed to the publisher — no decision is available until it reports back."
          : undefined
      }
      className="rounded bg-stone-100 px-3 py-1.5 text-xs font-medium text-slate-600"
    >
      {STATUS_PILLS[status] ?? status.replace(/_/g, " ")}
    </span>
  );
}
