"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { LineChart } from "@/components/LineChart";
import { api, describeDive, type Progress } from "@/lib/api";

const LABELS: Record<string, string> = {
  score_overall: "Overall score",
  score_takeoff: "Takeoff score",
  score_flight: "Flight score",
  score_entry: "Entry score",
  jump_height_m: "Jump height (m)",
  entry_angle_deg: "Entry angle from vertical (°)",
  min_hip_angle: "Tightest hip angle (°)",
  takeoff_knee_angle: "Knee extension at takeoff (°)",
  takeoff_hip_angle: "Hip extension at takeoff (°)",
  entry_body_line_deg: "Body line at entry (°)",
  flight_time_s: "Flight time (s)",
  rotation_deg: "Total rotation (°)",
};

const label = (metric: string) => LABELS[metric] ?? metric.replaceAll("_", " ");
const day = (x: number) => new Date(x).toLocaleDateString(undefined, { month: "short", day: "numeric" });

export default function ProgressPage() {
  const [progress, setProgress] = useState<Progress | null>(null);
  const [metric, setMetric] = useState("score_overall");
  const [error, setError] = useState("");

  useEffect(() => {
    api<Progress>("/progress")
      .then((p) => {
        setProgress(p);
        setError("");
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  const metrics = progress ? [...new Set(progress.daily.map((d) => d.metric))].sort((a, b) => label(a).localeCompare(label(b))) : [];
  const daily = progress?.daily.filter((d) => d.metric === metric) ?? [];

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Progress</h1>
      {error && <p className="text-red-600">{error}</p>}

      {progress && progress.dives.length === 0 && (
        <p className="text-slate-500">
          No dives yet.{" "}
          <Link href="/record" className="text-sky-600 hover:underline">
            Record one
          </Link>
          .
        </p>
      )}

      {progress && progress.dives.length > 0 && (
        <>
          <section className="rounded-lg border border-slate-200 p-4 dark:border-slate-800">
            <h2 className="mb-2 font-semibold">Score per dive</h2>
            <LineChart
              formatX={day}
              series={[
                {
                  name: "Overall score",
                  color: "#0ea5e9",
                  points: progress.dives.map((d) => ({ x: new Date(d.recorded_at).getTime(), y: d.overall_score })),
                },
              ]}
            />
          </section>

          <section className="rounded-lg border border-slate-200 p-4 dark:border-slate-800">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <h2 className="font-semibold">Daily average</h2>
              <select
                value={metric}
                onChange={(e) => setMetric(e.target.value)}
                className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900"
              >
                {metrics.map((m) => (
                  <option key={m} value={m}>
                    {label(m)}
                  </option>
                ))}
              </select>
            </div>
            <LineChart
              formatX={day}
              series={[{ name: label(metric), color: "#10b981", points: daily.map((d) => ({ x: new Date(d.bucket).getTime(), y: d.avg_value })) }]}
            />
          </section>

          <section>
            <h2 className="mb-2 font-semibold">Dives</h2>
            <ul className="divide-y divide-slate-200 rounded-lg border border-slate-200 dark:divide-slate-800 dark:border-slate-800">
              {[...progress.dives].reverse().map((d) => (
                <li key={d.id}>
                  <Link href={`/dives/${d.id}`} className="flex items-center justify-between gap-4 px-4 py-3 hover:bg-slate-50 dark:hover:bg-slate-900">
                    <div>
                      <div className="text-sm font-medium capitalize">{describeDive(d.setup)}</div>
                      <div className="text-xs text-slate-500">{new Date(d.recorded_at).toLocaleString()}</div>
                    </div>
                    <span className="text-lg font-semibold">{d.overall_score.toFixed(1)}</span>
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        </>
      )}
    </div>
  );
}
