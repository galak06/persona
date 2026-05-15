// Copied from social-comment-automation reference; trimmed for solo deploy.
//
// Tinted square icon container. Children are typically an SVG or emoji.
// Variant picks the tone; size picks the diameter.

import type { ReactNode } from "react";

interface IconBadgeProps {
  children: ReactNode;
  variant?: "primary" | "accent" | "warm" | "neutral";
  size?: "sm" | "md" | "lg";
  className?: string;
}

const VARIANT_STYLES: Record<NonNullable<IconBadgeProps["variant"]>, string> = {
  primary: "bg-amber-50 text-amber-800 border-amber-100",
  accent: "bg-orange-50 text-orange-700 border-orange-100",
  warm: "bg-rose-50 text-rose-800 border-rose-100",
  neutral: "bg-slate-50 text-slate-600 border-slate-200",
};

const SIZE_STYLES: Record<NonNullable<IconBadgeProps["size"]>, string> = {
  sm: "w-8 h-8 rounded-lg",
  md: "w-12 h-12 rounded-xl",
  lg: "w-14 h-14 rounded-2xl",
};

export default function IconBadge({
  children,
  variant = "primary",
  size = "md",
  className = "",
}: IconBadgeProps) {
  return (
    <div
      className={`flex items-center justify-center border ${VARIANT_STYLES[variant]} ${SIZE_STYLES[size]} ${className}`}
    >
      {children}
    </div>
  );
}
