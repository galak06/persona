// Copied from social-comment-automation reference; trimmed for solo deploy.

import type { ReactNode } from "react";

type Split = "equal" | "left-heavy";

interface ResponsiveCardRowProps {
  left: ReactNode;
  right: ReactNode;
  className?: string;
  split?: Split;
}

const SPLIT_CLASSES: Record<Split, string> = {
  equal: "md:grid-cols-2",
  "left-heavy": "md:grid-cols-[42%_1fr]",
};

export function ResponsiveCardRow({
  left,
  right,
  className = "",
  split = "equal",
}: ResponsiveCardRowProps): React.JSX.Element {
  return (
    <div
      className={`grid grid-cols-1 gap-4 ${SPLIT_CLASSES[split]} ${className}`.trim()}
    >
      <div className="min-w-0">{left}</div>
      <div className="min-w-0">{right}</div>
    </div>
  );
}
