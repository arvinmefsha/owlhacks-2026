/** Text colour for a 0–10 score, matching the dive review page. */
export function scoreColor(score: number | null) {
  if (score === null) return "text-slate-500";
  if (score >= 8) return "text-emerald-600";
  if (score >= 6) return "text-amber-600";
  return "text-red-600";
}
