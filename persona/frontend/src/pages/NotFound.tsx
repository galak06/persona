import { Link } from "react-router-dom";
import EmptyState from "../components/ui/EmptyState";

export default function NotFound() {
  return (
    <section className="bg-brand-surface rounded-2xl border border-brand-border shadow-card">
      <EmptyState
        title="Page not found"
        description="That route doesn't exist. Head back to the inbox to review pending items."
        actions={
          <Link
            to="/dashboard"
            className="inline-flex items-center px-4 py-2 rounded-lg bg-cyan-600 text-white text-sm font-semibold hover:bg-cyan-700"
          >
            Go to Dashboard
          </Link>
        }
      />
    </section>
  );
}
