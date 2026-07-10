import { useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { endpoints } from "../../api/endpoints";
import { useApiQuery } from "../../hooks/useApiQuery";
import { useBrand } from "../../context/BrandContext";
import type { components } from "../../types/openapi";

type PendingResponse = components["schemas"]["PendingResponse"];

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
    title: "Brands",
    items: [{ to: "/onboarding", label: "Onboarding", icon: "🏢" }],
  },
  {
    title: "System",
    items: [
      { to: "/connect", label: "Connect", icon: "🔗" },
      { to: "/operations", label: "Operations", icon: "🛠️" },
      { to: "/explorer", label: "Explorer", icon: "🗂️" },
    ],
  },
];

// Bottom tab bar primary items (most used)
const BOTTOM_TABS: NavItem[] = [
  { to: "/dashboard", label: "Home", icon: "🏠" },
  { to: "/inbox", label: "Inbox", icon: "📥" },
  { to: "/activity", label: "Activity", icon: "📊" },
  { to: "/ideas", label: "Ideas", icon: "💡" },
];

const POLL_MS = 5000;

const BASE_ITEM =
  "group flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition-all duration-150";
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
  const [drawerOpen, setDrawerOpen] = useState(false);
  const location = useLocation();

  const counts = pending?.counts ?? { blog_posts: 0, groups_to_join: 0 };
  const pendingTotal = counts.blog_posts + counts.groups_to_join;

  const brandName = config?.name || "Persona";

  // ── Desktop sidebar ────────────────────────────────────────────────
  return (
    <>
      <aside className="hidden md:flex w-60 shrink-0 border-r border-brand-border bg-brand-surface sticky top-0 h-screen overflow-y-auto flex-col">
        {/* Brand header */}
        <div className="px-5 pt-6 pb-5 border-b border-brand-border">
          <span className="font-display text-xl leading-tight font-semibold text-amber-800">
            {brandName}
          </span>
          {availableBrands.length > 1 && (
            <label className="mt-3 block">
              <span className="sr-only">Brand</span>
              <select
                value={selectedBrand}
                onChange={(e) => setSelectedBrand(e.target.value)}
                className="w-full text-sm border-stone-300 rounded-lg shadow-sm focus:border-amber-300 focus:ring focus:ring-amber-200/50"
              >
                {availableBrands.map((brand) => (
                  <option key={brand} value={brand}>{brand}</option>
                ))}
              </select>
            </label>
          )}
        </div>

        {/* Nav sections */}
        <nav className="flex-1 px-3 py-4 space-y-1" aria-label="Primary">
          {SECTIONS.map((section, i) => (
            <div key={section.title ?? `home-${i}`} className={i > 0 ? "pt-3" : ""}>
              {section.title && (
                <p className="px-3 mb-1 text-[10px] font-semibold uppercase tracking-widest text-slate-400">
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
                  <span aria-hidden="true" className="text-base leading-none w-5 text-center">
                    {item.icon}
                  </span>
                  <span className="flex-1">{item.label}</span>
                  {item.to === "/inbox" && pendingTotal > 0 && (
                    <Badge count={pendingTotal} />
                  )}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>

        {/* Footer */}
        <div className="px-5 py-4 border-t border-brand-border">
          <p className="text-[11px] text-slate-400 truncate">{config?.url || ""}</p>
        </div>
      </aside>

      {/* ── Mobile bottom tab bar ──────────────────────────────────────── */}
      <div className="md:hidden fixed bottom-0 inset-x-0 z-40 bg-white/95 backdrop-blur border-t border-brand-border safe-area-pb">
        <div className="flex items-stretch h-16">
          {BOTTOM_TABS.map((item) => {
            const isActive = location.pathname === item.to ||
              (item.to !== "/dashboard" && location.pathname.startsWith(item.to));
            return (
              <NavLink
                key={item.to}
                to={item.to}
                className="flex-1 flex flex-col items-center justify-center gap-0.5 text-[10px] font-medium transition-colors"
                style={{ color: isActive ? "var(--color-brand-primary)" : "#64748b" }}
                onClick={() => setDrawerOpen(false)}
              >
                <span className="text-xl leading-none">{item.icon}</span>
                <span>{item.label}</span>
                {item.to === "/inbox" && pendingTotal > 0 && (
                  <span className="absolute top-2 right-[calc(50%-18px)] inline-flex min-w-4 h-4 items-center justify-center rounded-full bg-amber-600 px-1 text-[9px] font-bold text-white">
                    {pendingTotal > 9 ? "9+" : pendingTotal}
                  </span>
                )}
              </NavLink>
            );
          })}

          {/* More button */}
          <button
            onClick={() => setDrawerOpen((v) => !v)}
            className="flex-1 flex flex-col items-center justify-center gap-0.5 text-[10px] font-medium text-slate-500"
          >
            <span className="text-xl leading-none">☰</span>
            <span>More</span>
          </button>
        </div>
      </div>

      {/* ── Mobile drawer ──────────────────────────────────────────────── */}
      {drawerOpen && (
        <>
          {/* Backdrop */}
          <div
            className="md:hidden fixed inset-0 z-40 bg-black/30 backdrop-blur-sm"
            onClick={() => setDrawerOpen(false)}
          />

          {/* Slide-up sheet */}
          <div className="md:hidden fixed bottom-16 inset-x-0 z-50 bg-white rounded-t-2xl shadow-floated overflow-hidden animate-slide-up">
            {/* Handle */}
            <div className="flex justify-center pt-3 pb-2">
              <div className="w-10 h-1 rounded-full bg-stone-200" />
            </div>

            {/* Brand name + brand selector */}
            <div className="px-5 pb-3 border-b border-brand-border flex items-center justify-between gap-3">
              <span className="font-display text-lg font-semibold text-amber-800 truncate">
                {brandName}
              </span>
              {availableBrands.length > 1 && (
                <select
                  value={selectedBrand}
                  onChange={(e) => setSelectedBrand(e.target.value)}
                  className="text-sm border-stone-300 rounded-lg focus:border-amber-300 focus:ring focus:ring-amber-200/50"
                >
                  {availableBrands.map((brand) => (
                    <option key={brand} value={brand}>{brand}</option>
                  ))}
                </select>
              )}
            </div>

            {/* All sections */}
            <nav className="px-4 py-3 grid grid-cols-2 gap-1 max-h-[60vh] overflow-y-auto pb-safe">
              {SECTIONS.flatMap((section) => section.items).map((item) => {
                const isActive = location.pathname === item.to ||
                  (item.to !== "/dashboard" && location.pathname.startsWith(item.to));
                return (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    onClick={() => setDrawerOpen(false)}
                    className={`flex items-center gap-2.5 rounded-xl px-3 py-3 text-sm transition-colors ${
                      isActive ? ACTIVE_ITEM : INACTIVE_ITEM
                    }`}
                  >
                    <span className="text-base w-5 text-center">{item.icon}</span>
                    <span className="flex-1 font-medium">{item.label}</span>
                    {item.to === "/inbox" && pendingTotal > 0 && (
                      <Badge count={pendingTotal} />
                    )}
                  </NavLink>
                );
              })}
            </nav>
          </div>
        </>
      )}
    </>
  );
}

function Badge({ count }: { count: number }) {
  return (
    <span className="inline-flex min-w-5 items-center justify-center rounded-full bg-amber-600 px-1.5 text-[10px] font-bold text-white tabular-nums">
      {count > 99 ? "99+" : count}
    </span>
  );
}
