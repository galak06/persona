import { Navigate, Route, Routes } from "react-router-dom";
import SideNav from "./components/layout/SideNav";
import { ToastProvider } from "./components/ui/Toast";
import Activity from "./pages/Activity";
import Campaigns from "./pages/Campaigns";
import Dashboard from "./pages/Dashboard";
import Ideas from "./pages/Ideas";
import Operations from "./pages/Operations";
import Inbox from "./pages/Inbox";
import Groups from "./pages/Groups";
import Published from "./pages/Published";
import Recipes from "./pages/Recipes";
import TikTokCandidates from "./pages/TikTokCandidates";
import Explorer from "./pages/Explorer";
import NotFound from "./pages/NotFound";

/**
 * Root shell. A grouped left sidebar (SideNav) plus the routed content
 * area: ten destinations organised into Home / Review / Engagement /
 * Operations, plus a 404 fallback. No auth gate, no tenant context: this
 * SPA only runs against a localhost backend on a trusted machine.
 */
export default function App() {
  return (
    <ToastProvider>
    <div className="min-h-screen bg-brand-bg flex">
      <SideNav />
      <main className="flex-1 min-w-0">
        <div className="mx-auto w-full max-w-5xl px-5 sm:px-8 py-8">
          <Routes>
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/activity" element={<Activity />} />
            <Route path="/campaigns" element={<Campaigns />} />
            <Route path="/inbox" element={<Inbox />} />
            <Route path="/ideas" element={<Ideas />} />
            <Route path="/groups" element={<Groups />} />
            <Route path="/published" element={<Published />} />
            <Route path="/tiktok" element={<TikTokCandidates />} />
            <Route path="/recipes" element={<Recipes />} />
            <Route path="/explorer" element={<Explorer />} />
            <Route path="/operations" element={<Operations />} />
            {/* Deep-link aliases — old tabs now open the matching segment. */}
            <Route path="/flows" element={<Operations initialView="health" />} />
            <Route
              path="/schedule"
              element={<Operations initialView="schedule" />}
            />
            <Route
              path="/flow-guide"
              element={<Operations initialView="audit" />}
            />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </div>
      </main>
    </div>
    </ToastProvider>
  );
}
