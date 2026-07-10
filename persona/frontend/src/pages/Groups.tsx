import { useEffect, useMemo, useState } from "react";
import { fetchGroups, updateGroup, type FacebookGroup } from "../api/groups";
import { getErrorMessage } from "../api/client";

type Bucket = "member" | "waiting" | "rejected" | "not_joined";

const BUCKETS: { id: Bucket; label: string; status: string }[] = [
  { id: "member", label: "Member", status: "joined" },
  { id: "waiting", label: "Waiting for admin", status: "join_requested" },
  { id: "rejected", label: "Rejected", status: "rejected" },
  { id: "not_joined", label: "Not joined yet", status: "not_joined_yet" },
];

export default function Groups() {
  const [groups, setGroups] = useState<FacebookGroup[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedBucket, setSelectedBucket] = useState<Bucket>("member");

  useEffect(() => {
    loadGroups();
  }, []);

  async function loadGroups() {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchGroups();
      setGroups(res.groups);
    } catch (err) {
      setError(getErrorMessage(err, "Failed to load groups"));
    } finally {
      setLoading(false);
    }
  }

  async function handleStatusChange(groupName: string, newStatus: string) {
    try {
      const updated = await updateGroup(groupName, { status: newStatus });
      setGroups((prev) =>
        prev.map((g) => (g.group_name === groupName ? updated : g))
      );
    } catch (err) {
      alert(getErrorMessage(err, "Failed to update status"));
    }
  }

  async function handleModeChange(groupName: string, newMode: string) {
    try {
      const updated = await updateGroup(groupName, { posting_mode: newMode });
      setGroups((prev) =>
        prev.map((g) => (g.group_name === groupName ? updated : g))
      );
    } catch (err) {
      alert(getErrorMessage(err, "Failed to update posting mode"));
    }
  }

  const counts = useMemo(() => {
    const c: Record<Bucket, number> = { member: 0, waiting: 0, rejected: 0, not_joined: 0 };
    for (const g of groups) {
      const bucket = BUCKETS.find((b) => b.status === g.status);
      if (bucket) c[bucket.id]++;
    }
    return c;
  }, [groups]);

  const filtered = useMemo(() => {
    const target = BUCKETS.find((b) => b.id === selectedBucket)?.status;
    return groups.filter((g) => g.status === target);
  }, [groups, selectedBucket]);

  if (loading) {
    return <div className="text-slate-500">Loading groups...</div>;
  }

  if (error) {
    return (
      <div className="bg-red-50 text-red-700 p-4 rounded-md">
        <h3 className="font-semibold mb-1">Error loading groups</h3>
        <p className="text-sm">{error}</p>
        <button
          onClick={loadGroups}
          className="mt-3 text-sm font-medium hover:underline"
        >
          Try Again
        </button>
      </div>
    );
  }

  const isMemberBucket = selectedBucket === "member";

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-xl font-bold text-slate-900">Facebook Groups</h1>
      <p className="text-slate-500 text-sm">
        Track group memberships and manage posting modes.
      </p>

      <div className="flex flex-wrap gap-2">
        {BUCKETS.map((b) => {
          const active = b.id === selectedBucket;
          return (
            <button
              key={b.id}
              onClick={() => setSelectedBucket(b.id)}
              className={
                "px-3 py-1.5 rounded-full text-sm font-medium border transition-colors " +
                (active
                  ? "bg-orange-50 text-orange-700 border-orange-200"
                  : "bg-white text-slate-600 border-slate-200 hover:bg-slate-50")
              }
            >
              {b.label} <span className="tabular-nums">({counts[b.id]})</span>
            </button>
          );
        })}
      </div>

      {filtered.length === 0 ? (
        <div className="text-slate-500 text-center py-12 bg-white rounded-md border border-slate-200">
          No groups in this bucket.
        </div>
      ) : (
        <div className="bg-white rounded-md border border-slate-200 overflow-x-auto">
          <table className="w-full text-left text-sm text-slate-700">
            <thead className="bg-slate-50 border-b border-slate-200 text-slate-500 font-medium">
              <tr>
                <th className="py-3 px-4 w-1/3">Group</th>
                <th className="py-3 px-4">Status</th>
                <th className="py-3 px-4">Mode</th>
                <th className="py-3 px-4 text-right">Members</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {filtered.map((g) => (
                <tr key={g.group_name} className="hover:bg-slate-50/50">
                  <td className="py-3 px-4 font-medium text-slate-900 truncate max-w-[300px]" title={g.group_name}>
                    {g.group_url ? (
                      <a
                        href={g.group_url}
                        target="_blank"
                        rel="noreferrer"
                        className="hover:underline hover:text-cyan-700"
                      >
                        {g.group_name}
                      </a>
                    ) : (
                      g.group_name
                    )}
                  </td>
                  <td className="py-3 px-4">
                    {isMemberBucket ? (
                      <select
                        value={g.status || "unknown"}
                        onChange={(e) => handleStatusChange(g.group_name, e.target.value)}
                        className="bg-transparent border border-slate-200 rounded py-1 px-2 text-sm focus:ring-1 focus:ring-cyan-500 focus:outline-none"
                      >
                        <option value="joined">Joined</option>
                        <option value="join_requested">Waiting for admin</option>
                        <option value="rejected">Rejected</option>
                      </select>
                    ) : (
                      <span className="text-slate-500">{g.status}</span>
                    )}
                  </td>
                  <td className="py-3 px-4">
                    {isMemberBucket ? (
                      <select
                        value={g.posting_mode || "direct"}
                        onChange={(e) => handleModeChange(g.group_name, e.target.value)}
                        className="bg-transparent border border-slate-200 rounded py-1 px-2 text-sm focus:ring-1 focus:ring-cyan-500 focus:outline-none"
                      >
                        <option value="direct">Direct</option>
                        <option value="needs_approval">Needs Approval</option>
                        <option value="paused">Paused</option>
                      </select>
                    ) : (
                      <span className="text-slate-400">—</span>
                    )}
                  </td>
                  <td className="py-3 px-4 text-right tabular-nums text-slate-500">
                    {g.member_count || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
