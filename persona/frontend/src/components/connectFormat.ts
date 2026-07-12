export function formatExpiry(iso: string | null): string {
  if (!iso) return "Never (page token)";
  const d = new Date(iso);
  const diff = Math.ceil((d.getTime() - Date.now()) / (1000 * 60 * 60 * 24));
  if (diff < 0) return `Expired ${Math.abs(diff)}d ago`;
  return `${d.toLocaleDateString()} (${diff}d left)`;
}
