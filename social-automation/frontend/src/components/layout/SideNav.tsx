import { NavLink } from "react-router-dom";
import { endpoints } from "../../api/endpoints";
import { useApiQuery } from "../../hooks/useApiQuery";
import { useBrand } from "../../context/BrandContext";
import type { PendingResponse } from "../../types/openapi";

/**
 * Grouped left sidebar.
 *
 * The ten destinations are organised into four scannable sections —
 * Home, Review, Engagement, Operations — so the daily-driver actions
 * (the approval Inbox, content) sit apart from the ops/audit views.
 * The Inbox link carries a live "needs action" badge polled from
 * `/pending`. No auth chrome: this UI runs against a localhost backend
 * on a trusted machine.
 */

interface NavItem {
  to: string;
  label: string;
  icon: string;
}

interface NavSection {
  title?: string;
  items: readonly NavItem[];
}

const SECTIONS: readonly NavSection[] = [
  { items: [{ to: "/dashboard", label: "Dashboard", icon: "🏠" }] },
  {
    title: "Review",
    items: [
      { to: "/inbox", label: "Inbox", icon: "📥" },
      { to: "/ideas", label: "Ideas", icon: "💡" },
      { to: "/recipes", label: "Recipes", icon: "🍲" },
      { to: "/campaigns", label: "Campaigns", icon: "📣" },
    ],
  },
  {
    title: "Engagement",
    items: [
      { to: "/activity", label: "Activity", icon: "📊" },
      { to: "/published", label: "Published", icon: "📤" },
      { to: "/groups", label: "FB Groups", icon: "👥" },
      { to: "/tiktok", label: "TikTok", icon: "🎵" },
    ],
  },
  {
    title: "System",
    items: [
      { to: "/operations", label: "Operations", icon: "🛠️" },
      { to: "/explorer", label: "Explorer", icon: "🗂️" },
    ],
  },
];

const POLL_MS = 5000;

const BASE_ITEM =
  "group flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors duration-150";
const ACTIVE_ITEM = "bg-amber-50 text-amber-900 font-semibold";
const INACTIVE_ITEM = "text-slate-600 hover:bg-stone-100 hover:text-slate-900";

interface ConfigResponse {
  name: string;
  url: string;
  persona: string;
  mascot: string;
}

export default function SideNav(): React.JSX.Element {
  const { data: config } = useApiQuery<ConfigResponse>(endpoints.config);
  const { selectedBrand, setSelectedBrand, availableBrands } = useBrand();
  const { data: pending } = useApiQuery<PendingResponse>(endpoints.pending, {
    refetchInterval: POLL_MS,
  });

  const counts = pending?.counts ?? { blog_posts: 0, groups_to_join: 0 };
  const pendingTotal = counts.blog_posts + counts.groups_to_join;

  return (
    <aside className="w-60 shrink-0 border-r border-brand-border bg-brand-surface sticky top-0 h-screen overflow-y-auto flex flex-col">
      <div className="px-5 pt-6 pb-5 border-b border-brand-border">
        <span className="font-display text-xl leading-tight font-semibold text-amber-800">
          {config?.name || "Loading…"}
        </span>
        <label className="mt-3 block">
          <span className="sr-only">Brand</span>
          <select
            value={selectedBrand}
            onChange={(e) => setSelectedBrand(e.target.value)}
            className="w-full text-sm border-stone-300 rounded-md shadow-sm focus:border-amber-300 focus:ring focus:ring-amber-200/50"
          >
            {availableBrands.map((brand) => (
              <option key={brand} value={brand}>
                {brand}
              </option>
            ))}
          </select>
        </label>
      </div>

      <nav className="flex-1 px-3 py-4 space-y-1" aria-label="Primary">
        {SECTIONS.map((section, i) => (
          <div key={section.title ?? `home-${i}`} className={i > 0 ? "pt-3" : ""}>
            {section.title && (
              <p className="px-3 mb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                {section.title}
              </p>
            )}
            {section.items.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  `${BASE_ITEM} ${isActive ? ACTIVE_ITEM : INACTIVE_ITEM}`
                }
              >
                <span aria-hidden="true" className="text-base leading-none">
                  {item.icon}
                </span>
                <span className="flex-1">{item.label}</span>
                {item.to === "/inbox" && pendingTotal > 0 && (
                  <span className="inline-flex min-w-5 items-center justify-center rounded-full bg-amber-600 px-1.5 text-xs font-semibold text-white tabular-nums">
                    {pendingTotal}
                  </span>
                )}
              </NavLink>
            ))}
          </div>
        ))}
      </nav>
    </aside>
  );
}
