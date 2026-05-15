import { Navigate, Route, Routes } from "react-router-dom";
import TopBar from "./components/layout/TopBar";
import Activity from "./pages/Activity";
import Dashboard from "./pages/Dashboard";
import Inbox from "./pages/Inbox";
import Groups from "./pages/Groups";
import NotFound from "./pages/NotFound";

/**
 * Root shell. Four routes — Dashboard, Activity, Inbox, Groups — plus a 404
 * fallback. No auth gate, no tenant context: this SPA only runs against a
 * localhost backend on a trusted machine.
 */
export default function App() {
  return (
    <div className="min-h-screen bg-brand-bg flex flex-col">
      <TopBar />
      <main className="flex-1 mx-auto w-full max-w-5xl px-4 sm:px-6 py-6">
        <Routes>
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/activity" element={<Activity />} />
          <Route path="/inbox" element={<Inbox />} />
          <Route path="/groups" element={<Groups />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
    </div>
  );
}
