"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useEffect, useMemo, useRef, useState } from "react";

import { UploadVideoPlayer } from "@/components/UploadVideoPlayer";
import { LineChart } from "@/components/LineChart";
import { api, describeDive, type Dive, type DiveFeedbackResult, type Metric, type Phase, type VisionReview, type Workout } from "@/lib/api";
import { drawPose } from "@/lib/pose-drawing";

const PHASES: Phase[] = ["takeoff", "flight", "entry"];

function formatValue(value: number | null, unit: string) {
  if (value === null) return "Unavailable";
  value = Number(value.toFixed(2));
  return unit.startsWith("°") || unit.startsWith("%") ? `${value}${unit}` : `${value} ${unit}`;
}

/** Index of the frame closest to time t (frames are sorted by time). */
function nearestFrame(times: number[], t: number) {
  let lo = 0;
  let hi = times.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (times[mid] < t) lo = mid + 1;
    else hi = mid;
  }
  return lo > 0 && t - times[lo - 1] < times[lo] - t ? lo - 1 : lo;
}

export default function DivePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const [dive, setDive] = useState<Dive | null>(null);
  const [workouts, setWorkouts] = useState<Workout[]>([]);
  const [error, setError] = useState("");
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    api<Dive>(`/dives/${id}`).then(setDive).catch((e: Error) => setError(e.message));
    api<Workout[]>("/workouts").then(setWorkouts).catch(() => {});
  }, [id]);

  useEffect(() => {
    if (dive?.feedback_source !== "pending") return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function checkFeedback() {
      try {
        const result = await api<DiveFeedbackResult>(`/dives/${id}/feedback`);
        if (!active) return;
        if (result.feedback_source !== "pending") {
          setDive((current) => current ? { ...current, ...result } : current);
          return;
        }
      } catch {
        // Keep the video review available and retry while the page is open.
      }
      if (active) timer = setTimeout(checkFeedback, 2000);
    }
    timer = setTimeout(checkFeedback, 2000);
    return () => { active = false; clearTimeout(timer); };
  }, [id, dive?.feedback_source]);

  function seek(t: number | null) {
    const video = videoRef.current;
    if (!video || t === null) return;
    video.pause();
    video.currentTime = t;
    video.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  async function remove() {
    if (!confirm("Delete this dive and its video?")) return;
    await api(`/dives/${id}`, { method: "DELETE" });
    router.push("/progress");
  }

  if (error) return <p className="text-red-600">{error}</p>;
  if (!dive) return <p className="text-slate-500">Loading dive…</p>;

  const { analysis, feedback } = dive;
  const macro = analysis.method === "macro-observations-v2";
  const legacy = !macro;
  const phaseTimes = { takeoff: analysis.phases.takeoff, apex: analysis.phases.apex, entry: analysis.phases.entry };
  const markers = Object.entries(phaseTimes).flatMap(([label, x]) => x === null ? [] : [{ label, x }]);
  const series = analysis.series;
  const points = (key: keyof typeof series) => series.t.map((t, i) => ({ x: t ?? 0, y: series[key]?.[i] ?? null }));
  const catalog = new Map(workouts.map((w) => [w.id, w]));

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold capitalize">{describeDive(dive.setup)}</h1>
          <p className="text-sm text-slate-500">{new Date(dive.recorded_at).toLocaleString()}</p>
        </div>
        <p className="max-w-xs text-right text-sm text-slate-500">
          {legacy ? "Saved with retired analysis" : "Evidence-based coaching observations"}
        </p>
      </header>

      <div className="grid gap-6 lg:grid-cols-[1fr_380px]">
        <section className="space-y-4">
          {dive.has_video ? (
            <DiveVideo dive={dive} videoRef={videoRef} />
          ) : (
            <p className="rounded-md bg-slate-100 p-4 text-sm text-slate-500 dark:bg-slate-900">No video was saved for this dive.</p>
          )}
          {macro && <div className="flex flex-wrap gap-2 text-sm">
            {Object.entries(phaseTimes).map(([label, t]) => (
              <button key={label} disabled={t === null} onClick={() => seek(t)} className="rounded-md border border-slate-300 px-3 py-1.5 capitalize disabled:opacity-50 dark:border-slate-700">
                {label} · {t === null ? "Unavailable" : `${t.toFixed(2)} s`}
              </button>
            ))}
          </div>}
          {macro && <div className="space-y-4 rounded-lg border border-slate-200 p-4 dark:border-slate-800">
            <h2 className="font-semibold">Envelope compaction</h2>
            <LineChart
              unit=" ratio"
              markers={markers}
              onPick={seek}
              series={[{ name: "Box height / standing height", color: "#0ea5e9", points: points("box_height_ratio") }]}
            />
            <h2 className="font-semibold">Top of body above board</h2>
            <LineChart unit=" m" markers={markers} onPick={seek} series={[{ name: "Box top", color: "#10b981", points: points("box_top_height_m") }]} />
          </div>}
        </section>

        <aside className="space-y-5">
          {legacy ? (
            <div className="rounded-lg border border-slate-200 p-4 text-sm dark:border-slate-800">
              <h2 className="font-semibold">Coaching feedback retired</h2>
              <p className="mt-1 text-slate-600 dark:text-slate-400">The old feedback depended on the same incorrect measurements, so it is hidden rather than presented as reliable advice.</p>
            </div>
          ) : <>
          {dive.feedback_source === "pending" && (
            <div className="rounded-lg border border-sky-200 bg-sky-50 p-4 text-sm dark:border-sky-900 dark:bg-sky-950" role="status">
              Gemini coaching is still processing. The rule-based feedback below will update automatically when it finishes.
            </div>
          )}
          <div>
            <div className="mb-1 flex items-center gap-2">
              <h2 className="font-semibold">Coach&apos;s summary</h2>
              <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-500 dark:bg-slate-800">
                {dive.feedback_source === "gemini" ? "Gemini" : dive.feedback_source === "pending" ? "Rule-based · Gemini pending" : "Rule-based"}
              </span>
            </div>
            <p className="text-sm leading-relaxed">{feedback.summary}</p>
          </div>

          {feedback.faults.length > 0 && (
            <div>
              <h2 className="mb-2 font-semibold">What to fix</h2>
              <ul className="space-y-2">
                {feedback.faults.map((f) => (
                  <li key={f.title} className="rounded-md border border-slate-200 p-3 text-sm dark:border-slate-800">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium">{f.title}</span>
                      <span className={`text-xs ${f.severity === "major" ? "text-red-600" : "text-amber-600"}`}>{f.severity}</span>
                    </div>
                    <p className="mt-1 text-slate-600 dark:text-slate-400">{f.detail}</p>
                    {f.timestamp_s !== null && (
                      <button onClick={() => seek(f.timestamp_s)} className="mt-1 text-xs text-sky-600 hover:underline">
                        Show at {f.timestamp_s.toFixed(2)} s ({f.phase})
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div>
            <h2 className="mb-2 font-semibold">Cues for next time</h2>
            <ul className="list-disc space-y-1 pl-5 text-sm">
              {feedback.cues.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
          </div>

          <div>
            <h2 className="mb-2 font-semibold">Workouts</h2>
            {feedback.workouts.length === 0 && <p className="text-sm text-slate-500">No corrective workout assigned without a supported fault.</p>}
            <ul className="space-y-2">
              {feedback.workouts.map((pick) => {
                const w = catalog.get(pick.id);
                return (
                  <li key={pick.id} className="rounded-md border border-slate-200 p-3 text-sm dark:border-slate-800">
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="font-medium">{w?.name ?? pick.id}</span>
                      {w && (
                        <span className="text-xs text-slate-500">
                          {w.sets} × {w.reps}
                        </span>
                      )}
                    </div>
                    <p className="mt-1 text-slate-600 dark:text-slate-400">{pick.reason}</p>
                    {w && <p className="mt-1 text-xs text-slate-500">{w.description} Equipment: {w.equipment}.</p>}
                  </li>
                );
              })}
            </ul>
          </div>
          </>}
        </aside>
      </div>

      {macro && <section className="space-y-3">
        <h2 className="font-semibold">All measurements</h2>
        <div className="grid gap-4 md:grid-cols-3">
          {PHASES.map((phase) => (
            <MetricTable key={phase} phase={phase} metrics={analysis.metrics.filter((m) => m.phase === phase)} onSeek={seek} />
          ))}
        </div>
        <p className="text-sm text-slate-500">
          {analysis.info.map((i) => `${i.label}: ${formatValue(i.value, i.unit)}`).join(" · ")}
          {analysis.rotation.measured_deg !== null && ` · Expected rotation: ${analysis.rotation.expected_deg}°`}
        </p>
      </section>}

      {macro && dive.has_video && <VisionReviewPanel dive={dive} videoRef={videoRef} />}

      <div className="flex flex-wrap items-center gap-3 border-t border-slate-200 pt-4 text-sm dark:border-slate-800">
        <Link href="/upload" className="min-h-11 rounded-lg bg-sky-700 px-4 py-2 font-semibold text-white hover:bg-sky-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-600">
          Upload another dive
        </Link>
        <Link href="/record" className="min-h-11 rounded-lg border border-slate-300 px-4 py-2 font-semibold hover:bg-slate-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-600 dark:border-slate-700 dark:hover:bg-slate-800">
          Record another dive
        </Link>
        <Link href="/progress" className="text-sky-600 hover:underline">
          See progress
        </Link>
        <button onClick={remove} className="ml-auto text-red-600 hover:underline">
          Delete dive
        </button>
      </div>
    </div>
  );
}

function MetricTable({ phase, metrics, onSeek }: { phase: Phase; metrics: Metric[]; onSeek: (t: number | null) => void }) {
  if (metrics.length === 0) return null;
  return (
    <div className="rounded-lg border border-slate-200 p-3 dark:border-slate-800">
      <h3 className="mb-2 text-sm font-semibold capitalize">{phase}</h3>
      <ul className="space-y-2 text-sm">
        {metrics.map((m) => (
          <li key={m.key}>
            <button onClick={() => onSeek(m.t)} className="w-full text-left" disabled={m.t === null}>
              <div className="flex justify-between gap-2">
                <span>{m.label}</span>
                <span className={`font-medium ${m.status === "flagged" ? "text-red-600" : m.status === "within_target" ? "text-emerald-600" : "text-slate-500"}`}>{m.status?.replaceAll("_", " ") ?? "Unavailable"}</span>
              </div>
              <div className="flex justify-between gap-2 text-xs text-slate-500">
                <span>{formatValue(m.value, m.unit)}</span>
                <span>target {m.target}</span>
              </div>
              {m.reason && <p className="mt-1 text-xs text-slate-500">{m.reason}</p>}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function DiveVideo({ dive, videoRef }: { dive: Dive; videoRef: React.RefObject<HTMLVideoElement | null> }) {
  const frames = useMemo(() => dive.frames.t.map((t, index) => {
    const flat = dive.frames.lm[index];
    return { t, lm: flat ? Array.from({ length: 17 }, (_, i) => flat.slice(i * 4, i * 4 + 4)) : null };
  }), [dive.frames]);
  return <UploadVideoPlayer src={`/api/dives/${dive.id}/video`} frames={frames} calibration={dive.calibration} videoRef={videoRef} />;
}

async function captureKeyframe(video: HTMLVideoElement, dive: Dive, t: number): Promise<Blob> {
  video.pause();
  await new Promise((resolve) => {
    video.addEventListener("seeked", resolve, { once: true });
    video.currentTime = t;
  });
  const canvas = document.createElement("canvas");
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  const ctx = canvas.getContext("2d")!;
  ctx.drawImage(video, 0, 0);
  const lm = dive.frames.lm[nearestFrame(dive.frames.t, t)];
  if (lm) drawPose(ctx, Array.from({ length: 17 }, (_, index) => lm.slice(index * 4, index * 4 + 4)), dive.calibration.water_y);
  return new Promise((resolve, reject) => canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("Capture failed"))), "image/jpeg", 0.85));
}

function VisionReviewPanel({ dive, videoRef }: { dive: Dive; videoRef: React.RefObject<HTMLVideoElement | null> }) {
  const [review, setReview] = useState<VisionReview | null>(dive.vision_review);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function run() {
    const video = videoRef.current;
    if (!video) return;
    setBusy(true);
    setError("");
    try {
      const form = new FormData();
      const { takeoff, apex, entry } = dive.analysis.phases;
      if (takeoff === null || apex === null || entry === null) throw new Error("All three milestones must be visible before requesting a visual review.");
      for (const [name, t] of [["takeoff", takeoff], ["apex", apex], ["entry", entry]] as const) {
        form.append(name, await captureKeyframe(video, dive, t), `${name}.jpg`);
      }
      setReview(await api<VisionReview>(`/dives/${dive.id}/vision-review`, { method: "POST", body: form }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="space-y-2 rounded-lg border border-slate-200 p-4 dark:border-slate-800">
      <div className="flex items-center justify-between gap-2">
        <div>
          <h2 className="font-semibold">Visual review</h2>
          <p className="text-sm text-slate-500">Gemini looks at the takeoff, top and entry frames for things the measurements miss.</p>
        </div>
        <button onClick={run} disabled={busy || [dive.analysis.phases.takeoff, dive.analysis.phases.apex, dive.analysis.phases.entry].some(t => t === null)} className="rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-white dark:text-slate-900">
          {busy ? "Reviewing…" : review ? "Review again" : "Run visual review"}
        </button>
      </div>
      {error && <p className="text-sm text-red-600">{error}</p>}
      {review && (
        <div className="space-y-2 text-sm">
          <p>{review.summary}</p>
          <ul className="list-disc space-y-1 pl-5">
            {review.notes.map((n) => (
              <li key={n.note}>
                <span className="font-medium capitalize">{n.phase}:</span> {n.note}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
