import { NavLink } from "react-router-dom";
import { endpoints } from "../../api/endpoints";
import { useApiQuery } from "../../hooks/useApiQuery";
import { useBrand } from "../../context/BrandContext";

/**
 * Three-tab top navigation: Dashboard | Activity | Inbox.
 *
 * Active tab uses a soft cyan pill (`bg-cyan-50 text-cyan-700`); inactive
 * tabs are slate text with no background. No wordmark and no auth chrome
 * — this UI runs against a localhost backend on a trusted machine.
 */

interface TabSpec {
  to: string;
  label: string;
}

const TABS: readonly TabSpec[] = [
  { to: "/dashboard", label: "Dashboard" },
  { to: "/activity", label: "Activity" },
  { to: "/flows", label: "Flows" },
  { to: "/campaigns", label: "Campaigns" },
  { to: "/inbox", label: "Inbox" },
  { to: "/groups", label: "FB Groups" },
  { to: "/schedule", label: "Schedule" },
  { to: "/explorer", label: "Explorer" },
  { to: "/flow-guide", label: "Flow Guide" },
];

const BASE_TAB =
  "px-5 py-2 rounded-full text-sm transition-colors duration-150";
const ACTIVE_TAB = "bg-cyan-50 text-cyan-700 font-medium";
const INACTIVE_TAB = "text-slate-500 hover:text-slate-700";

interface ConfigResponse {
  name: string;
  url: string;
  persona: string;
  mascot: string;
}

export default function TopBar(): React.JSX.Element {
  const { data: config } = useApiQuery<ConfigResponse>(endpoints.config);
  const { selectedBrand, setSelectedBrand, availableBrands } = useBrand();

  return (
    <header className="bg-brand-header border-b border-slate-200">
      <div className="mx-auto w-full max-w-5xl px-4 sm:px-6 h-14 flex items-center justify-between">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <span className="text-sm font-bold text-amber-700 uppercase tracking-wider">
              {config?.name || "Loading..."}
            </span>
            <select
              value={selectedBrand}
              onChange={(e) => setSelectedBrand(e.target.value)}
              className="ml-2 text-sm border-gray-300 rounded-md shadow-sm focus:border-amber-300 focus:ring focus:ring-amber-200 focus:ring-opacity-50"
            >
              {availableBrands.map((brand) => (
                <option key={brand} value={brand}>
                  {brand}
                </option>
              ))}
            </select>
          </div>
          <nav className="flex items-center gap-1" aria-label="Primary">
            {TABS.map((tab) => (
              <NavLink
                key={tab.to}
                to={tab.to}
                className={({ isActive }) =>
                  `${BASE_TAB} ${isActive ? ACTIVE_TAB : INACTIVE_TAB}`
                }
              >
                {tab.label}
              </NavLink>
            ))}
          </nav>
        </div>
      </div>
    </header>
  );
}
