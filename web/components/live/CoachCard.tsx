"use client";

import { describeDive, type Dive, type LiveTip } from "@/lib/api";
import { speakTip } from "@/lib/live-speech";
import { scoreColor } from "@/lib/score";

const button = "min-h-11 rounded-lg border border-slate-300 px-4 py-2 text-sm font-semibold hover:bg-slate-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-600 disabled:opacity-40 dark:border-slate-600 dark:hover:bg-slate-800";

export function CoachCard({ number, dive, tip, tipSource, tipError }: {
  number: number;
  dive: Dive;
  tip: string | null;
  tipSource: LiveTip["source"] | null;
  tipError: string;
}) {
  return (
    <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-950" aria-label={`Dive #${number} coaching`}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Dive #{number}</h2>
          <p className="text-xs capitalize text-slate-500">{describeDive(dive.setup)}</p>
        </div>
        <div className="text-center">
          <div className={`text-3xl font-bold ${scoreColor(dive.overall_score)}`}>{dive.overall_score.toFixed(1)}</div>
          <div className="text-xs text-slate-500">overall / 10</div>
        </div>
      </div>
      <div className="rounded-lg bg-slate-100 p-3 dark:bg-slate-900">
        <div className="mb-1 flex items-center gap-2">
          <h3 className="text-sm font-semibold">Coach&apos;s tip</h3>
          {tipSource && (
            <span className="rounded bg-white px-1.5 py-0.5 text-xs text-slate-500 dark:bg-slate-800">{tipSource === "gemini" ? "Gemini" : "Rule-based"}</span>
          )}
        </div>
        {tip ? (
          <p className="text-sm leading-relaxed">{tip}</p>
        ) : tipError ? (
          <p className="text-sm text-slate-500">{tipError}</p>
        ) : (
          <p role="status" className="animate-pulse text-sm text-slate-500">Writing your tip…</p>
        )}
      </div>
      <button className={button} disabled={!tip} onClick={() => { if (tip) void speakTip(tip); }}>Replay tip</button>
    </section>
  );
}
