/**
 * Presentational primitives for the Dashboard tiles.
 *
 * No data fetching and no derivation logic — these take finished numbers
 * and strings. The rules that produce them live in `summary.ts`.
 */

import { Link } from "react-router-dom";

import type { Tone } from "./summary";

const SURFACE =
  "bg-brand-surface rounded-2xl border border-brand-border shadow-card";

const TONE_TEXT: Record<Tone, string> = {
  ok: "text-emerald-700",
  warn: "text-amber-700",
  bad: "text-rose-700",
  idle: "text-slate-900",
};

const TONE_DOT: Record<Tone, string> = {
  ok: "bg-emerald-500",
  warn: "bg-amber-500",
  bad: "bg-rose-500",
  idle: "bg-slate-300",
};

interface TileProps {
  label: string;
  icon: string;
  to?: string;
  linkText?: string;
  children: React.ReactNode;
}

/** Shared tile chrome: uppercase label, emoji, optional "open" link. */
export function Tile({
  label,
  icon,
  to,
  linkText = "Open →",
  children,
}: TileProps): React.JSX.Element {
  return (
    <div className={`${SURFACE} px-5 py-5 flex flex-col`}>
      <div className="flex items-start justify-between gap-3">
        <p className="text-xs uppercase tracking-wide text-slate-400 font-semibold">
          {label}
        </p>
        <span aria-hidden="true" className="text-xl leading-none">
          {icon}
        </span>
      </div>
      <div className="mt-3 flex-1">{children}</div>
      {to && (
        <Link
          to={to}
          className="text-xs font-medium text-cyan-700 hover:text-cyan-800 mt-3"
        >
          {linkText}
        </Link>
      )}
    </div>
  );
}

interface HeadlineProps {
  value: string;
  tone?: Tone;
  caption?: string;
}

/** The one big number (or phrase) a tile leads with. */
export function Headline({
  value,
  tone = "idle",
  caption,
}: HeadlineProps): React.JSX.Element {
  return (
    <>
      <p
        className={`text-3xl font-semibold leading-none tabular-nums ${TONE_TEXT[tone]}`}
      >
        {value}
      </p>
      {caption && <p className="text-sm text-slate-500 mt-2">{caption}</p>}
    </>
  );
}

interface StatusLineProps {
  tone: Tone;
  label: string;
  value: string;
}

/** One `● platform … value` row inside a tile. */
export function StatusLine({
  tone,
  label,
  value,
}: StatusLineProps): React.JSX.Element {
  return (
    <div className="flex items-center justify-between gap-2 py-0.5">
      <span className="flex items-center gap-2 text-sm text-slate-600 capitalize">
        <span
          aria-hidden="true"
          className={`h-2 w-2 rounded-full shrink-0 ${TONE_DOT[tone]}`}
        />
        {label}
      </span>
      <span className={`text-sm font-medium tabular-nums ${TONE_TEXT[tone]}`}>
        {value}
      </span>
    </div>
  );
}

interface MiniStatProps {
  label: string;
  value: number;
  tone?: Tone;
}

export function MiniStat({
  label,
  value,
  tone = "idle",
}: MiniStatProps): React.JSX.Element {
  return (
    <div className="rounded-xl border border-brand-border bg-stone-50/40 px-3 py-2">
      <p className="text-[11px] uppercase tracking-wide text-slate-400 font-semibold">
        {label}
      </p>
      <p
        className={`text-2xl font-semibold leading-none mt-1 tabular-nums ${TONE_TEXT[tone]}`}
      >
        {value}
      </p>
    </div>
  );
}

interface PanelProps {
  title: string;
  to?: string;
  linkText?: string;
  children: React.ReactNode;
}

/** Full-width section below the tile row. */
export function Panel({
  title,
  to,
  linkText = "View all →",
  children,
}: PanelProps): React.JSX.Element {
  return (
    <div className={`${SURFACE} px-5 py-5`}>
      <div className="flex items-baseline justify-between gap-3 flex-wrap">
        <h2 className="text-xs uppercase tracking-wide text-slate-400 font-semibold">
          {title}
        </h2>
        {to && (
          <Link
            to={to}
            className="text-xs font-medium text-cyan-700 hover:text-cyan-800"
          >
            {linkText}
          </Link>
        )}
      </div>
      <div className="mt-3">{children}</div>
    </div>
  );
}
