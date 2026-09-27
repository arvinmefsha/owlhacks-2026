"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, describeDive, type DiveSummary, type Progress } from "@/lib/api";

function change(dive: DiveSummary, previous: DiveSummary | undefined) {
  if (!previous) return "First recorded dive";
  const currentFocus = new Set(dive.markers.focus);
  const priorFocus = new Set(previous.markers.focus);
  const resolved = [...priorFocus].filter((item) => !currentFocus.has(item));
  const newFocus = [...currentFocus].filter((item) => !priorFocus.has(item));
  const gained = dive.markers.strengths.filter((item) => !previous.markers.strengths.includes(item));
  if (resolved.length) return `Improved: ${resolved[0]}`;
  if (gained.length) return `New strength: ${gained[0]}`;
  if (newFocus.length) return `New focus: ${newFocus[0]}`;
  return "Similar observations to the prior dive";
}

export default function ProgressPage() {
  const [progress, setProgress] = useState<Progress | null>(null);
  const [error, setError] = useState("");
  const [deleting, setDeleting] = useState(false);
  useEffect(() => { api<Progress>("/progress").then(setProgress).catch((e: Error) => setError(e.message)); }, []);

  async function deleteAll() {
    if (!confirm("Delete all saved dives, videos, tracking data, and readiness data? This cannot be undone.")) return;
    setDeleting(true);
    try { await api("/dives", { method: "DELETE" }); setProgress({ dives: [], daily: [] }); }
    catch (e) { setError((e as Error).message); }
    finally { setDeleting(false); }
  }

  const dives = progress?.dives.filter((d) => d.analysis_method === "macro-observations-v2") ?? [];
  return <div className="space-y-6">
    <div className="flex flex-wrap items-center justify-between gap-3"><h1 className="text-xl font-semibold">Progress</h1><button onClick={deleteAll} disabled={deleting} className="rounded-md border border-red-300 px-3 py-2 text-sm font-medium text-red-700 hover:bg-red-50 disabled:opacity-50 dark:border-red-800 dark:text-red-300 dark:hover:bg-red-950">{deleting ? "Deleting…" : "Delete all dives and data"}</button></div>
    {error && <p className="text-red-600">{error}</p>}
    {progress && progress.dives.length === 0 && <p className="text-slate-500">No dives yet. <Link href="/record" className="text-sky-600 hover:underline">Record one</Link>.</p>}
    {progress && progress.dives.length > 0 && <>
      <section className="rounded-lg border border-slate-200 p-4 dark:border-slate-800"><h2 className="font-semibold">Your coaching timeline</h2><p className="mt-1 text-sm text-slate-500">Each dive records visible strengths and the next focus. Progress appears when a prior focus clears or a new strength becomes consistent.</p></section>
      {dives.length === 0 ? <p className="text-slate-500">New uploads will appear here as coaching observations.</p> : <ol className="space-y-3">{dives.map((dive, index) => {
        const prior = dives[index - 1];
        return <li key={dive.id} className="rounded-lg border border-slate-200 p-4 dark:border-slate-800"><Link href={`/dives/${dive.id}`} className="block hover:text-sky-600"><div className="flex flex-wrap justify-between gap-2"><span className="font-medium capitalize">{describeDive(dive.setup)}</span><span className="text-sm text-slate-500">{new Date(dive.recorded_at).toLocaleString()}</span></div><p className="mt-2 text-sm font-medium text-sky-700 dark:text-sky-300">{change(dive, prior)}</p><div className="mt-3 grid gap-2 text-sm sm:grid-cols-2"><p><span className="text-slate-500">Strengths: </span>{dive.markers.strengths.slice(0, 2).join(" · ") || "No clear strength recorded yet"}</p><p><span className="text-slate-500">Next focus: </span>{dive.markers.focus.slice(0, 2).join(" · ") || "No supported focus area"}</p></div></Link></li>;
      })}</ol>}
    </>}
  </div>;
}
