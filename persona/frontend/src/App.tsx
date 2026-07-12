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
import Onboarding from "./pages/Onboarding";
import BrandSettings from "./pages/BrandSettings";
import Published from "./pages/Published";
import Recipes from "./pages/Recipes";
import TikTokCandidates from "./pages/TikTokCandidates";
import Connect from "./pages/Connect";
import Explorer from "./pages/Explorer";
import NotFound from "./pages/NotFound";

export default function App() {
  return (
    <ToastProvider>
      <div className="min-h-screen bg-brand-bg flex">
        <SideNav />
        {/* pb-16 on mobile to clear the fixed bottom tab bar */}
        <main className="flex-1 min-w-0 pb-16 md:pb-0">
          <div className="mx-auto w-full max-w-5xl px-4 sm:px-6 md:px-8 py-5 md:py-8">
            <Routes>
              <Route path="/" element={<Navigate to="/dashboard" replace />} />
              <Route path="/dashboard" element={<Dashboard />} />
              <Route path="/activity" element={<Activity />} />
              <Route path="/campaigns" element={<Campaigns />} />
              <Route path="/inbox" element={<Inbox />} />
              <Route path="/ideas" element={<Ideas />} />
              <Route path="/groups" element={<Groups />} />
              <Route path="/published" element={<Published />} />
              <Route path="/onboarding" element={<Onboarding />} />
              <Route path="/onboarding/:id/settings" element={<BrandSettings />} />
              <Route path="/onboarding/:id/connect" element={<Connect />} />
              <Route path="/tiktok" element={<TikTokCandidates />} />
              <Route path="/recipes" element={<Recipes />} />
              <Route path="/connect" element={<Connect />} />
              <Route path="/explorer" element={<Explorer />} />
              <Route path="/operations" element={<Operations />} />
              <Route path="/flows" element={<Operations initialView="health" />} />
              <Route path="/schedule" element={<Operations initialView="schedule" />} />
              <Route path="/flow-guide" element={<Operations initialView="audit" />} />
              <Route path="*" element={<NotFound />} />
            </Routes>
          </div>
        </main>
      </div>
    </ToastProvider>
  );
}
