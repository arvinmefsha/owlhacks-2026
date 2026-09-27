"use client";

import Link from "next/link";

import { scoreColor } from "@/lib/score";
import type { LiveDive } from "./LiveSession";

const button = "inline-flex min-h-11 items-center justify-center rounded-lg border border-slate-300 px-4 py-2 text-sm font-semibold hover:bg-slate-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-600 dark:border-slate-600 dark:hover:bg-slate-800";

/** End-of-session summary: every dive in order, its score, and a link to the full analysis. */
export function SessionTimeline({ dives, onNewSession }: { dives: LiveDive[]; onNewSession: () => void }) {
  const analyzing = dives.filter((dive) => dive.status === "analyzing").length;
  return (
    <section className="space-y-4" aria-labelledby="session-summary-title">
      <header className="rounded-xl bg-slate-100 p-5 dark:bg-slate-900">
        <p className="text-xs font-semibold uppercase tracking-widest text-sky-700 dark:text-sky-300">Session summary</p>
        <h1 id="session-summary-title" className="mt-1 text-2xl font-semibold">
          {dives.length === 0 ? "No dives recorded" : `${dives.length} ${dives.length === 1 ? "dive" : "dives"} this session`}
        </h1>
        <p className="mt-2 text-sm leading-relaxed text-slate-600 dark:text-slate-300" role="status">
          {dives.length === 0
            ? "Dives are saved automatically once the camera sees a diver leave the board."
            : analyzing > 0
              ? `${analyzing} still analyzing. Scores fill in here as each one finishes.`
              : "Open any dive for the full breakdown."}
        </p>
      </header>

      {dives.length > 0 && (
        <ol className="flex snap-x gap-4 overflow-x-auto pb-3" aria-label="Dives in this session">
          {dives.map((dive, index) => (
            <li key={dive.number} className="flex w-44 shrink-0 snap-start flex-col">
              <div className="mb-2 flex items-center" aria-hidden="true">
                <span className="h-3 w-3 shrink-0 rounded-full bg-sky-600" />
                {index < dives.length - 1 && <span className="-mr-4 ml-1 h-px flex-1 bg-slate-300 dark:bg-slate-700" />}
              </div>
              <article className="flex flex-1 flex-col gap-3 rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-950">
                <h2 className="font-semibold">Dive #{dive.number}</h2>
                <DiveResult dive={dive} />
                {dive.diveId && (
                  <Link href={`/dives/${dive.diveId}`} className={`${button} mt-auto`}>Full analysis</Link>
                )}
              </article>
            </li>
          ))}
        </ol>
      )}

      <button className={`${button} border-sky-700 bg-sky-700 text-white hover:bg-sky-800 dark:border-sky-700 dark:hover:bg-sky-800`} onClick={onNewSession}>
        Start a new session
      </button>
    </section>
  );
}

function DiveResult({ dive }: { dive: LiveDive }) {
  if (dive.status === "complete" && dive.dive) {
    return (
      <p>
        <span className={`text-3xl font-bold ${scoreColor(dive.dive.overall_score)}`}>{dive.dive.overall_score.toFixed(1)}</span>
        <span className="text-xs text-slate-500"> / 10</span>
      </p>
    );
  }
  if (dive.status === "analyzing") {
    const percent = Math.round(dive.progress * 100);
    return (
      <div className="space-y-1">
        <p className="text-sm font-medium">Analyzing… {percent}%</p>
        <div className="h-1.5 overflow-hidden rounded bg-sky-100 dark:bg-sky-900">
          <div className="h-full bg-sky-600 transition-all" style={{ width: `${percent}%` }} />
        </div>
        {dive.stage && <p className="text-xs text-slate-500">{dive.stage}</p>}
      </div>
    );
  }
  return <p className="text-sm text-red-700 dark:text-red-300">{dive.error || "Analysis did not complete."}</p>;
}
