"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { LiveSession } from "@/components/live/LiveSession";
import { UploadCapture } from "@/components/UploadCapture";
import { api, type AnalysisJob, type Calibration, type DiveSetup, type Point } from "@/lib/api";

const DEFAULT_SETUP: DiveSetup = {
  position: "tuck",
  direction: "forward",
  somersaults: 1.5,
  apparatus: "springboard",
  board_height_m: 1,
};

type TapMode = "board" | "water" | null;
type Capture = { video: Blob; filename: string; source: "live" | "upload" };
type CaptureProps = {
  calibration: Calibration;
  tapMode: TapMode;
  onTap: (point: Point) => void;
  onDone: (capture: Capture) => void;
  disabled: boolean;
};

const selectClass = "mt-1 block w-full rounded-md border border-slate-300 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-900";

export function DiveCapturePage({ initialMode = "live" }: { initialMode?: "live" | "upload" }) {
  const router = useRouter();
  const [setup, setSetup] = useState<DiveSetup>(DEFAULT_SETUP);
  const [calibration, setCalibration] = useState<Calibration>({ board_tip: null, water_y: null, roi: null });
  const [tapMode, setTapMode] = useState<TapMode>(null);
  const mode = initialMode;
  const [liveActive, setLiveActive] = useState(false);
  const [job, setJob] = useState<AnalysisJob | null>(null);
  const [error, setError] = useState("");

  function onTap(point: Point) {
    if (tapMode === "board") setCalibration((current) => ({ ...current, board_tip: point }));
    if (tapMode === "water") setCalibration((current) => ({ ...current, water_y: point.y }));
    setTapMode(null);
  }

  async function submit(capture: Capture) {
    if (!calibration.board_tip || calibration.water_y === null) {
      return setError("Mark the board tip and visible air–water surface first.");
    }
    setError("");
    const form = new FormData();
    form.append("payload", new Blob([JSON.stringify({
      setup,
      calibration,
      source: capture.source,
      profile: capture.source === "live" ? "fast" : "quality",
    })], { type: "application/json" }), "payload.json");
    form.append("video", capture.video, capture.filename);
    try {
      const created = await api<{ id: string }>("/analysis/jobs", { method: "POST", body: form });
      for (;;) {
        const next = await api<AnalysisJob>(`/analysis/jobs/${created.id}`);
        setJob(next);
        if (next.status === "complete" && next.dive_id) {
          router.push(`/dives/${next.dive_id}`);
          return;
        }
        if (next.status === "failed" || next.status === "cancelled") {
          throw new Error(next.error || "Analysis did not complete.");
        }
        await new Promise((resolve) => setTimeout(resolve, 750));
      }
    } catch (caught) {
      setError((caught as Error).message);
      setJob(null);
    }
  }

  const update = <K extends keyof DiveSetup>(key: K, value: DiveSetup[K]) => setSetup((current) => ({ ...current, [key]: value }));
  const disabled = job !== null && !["complete", "failed", "cancelled"].includes(job.status);
  const captureProps: CaptureProps = { calibration, tapMode, onTap, onDone: submit, disabled };

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
      <section className="space-y-3">
        {mode === "live" ? <LiveSession setup={setup} onActiveChange={setLiveActive} /> : <UploadCapture {...captureProps} />}
        {job && disabled && (
          <div className="space-y-2 rounded-lg border border-sky-300 bg-sky-50 p-4 dark:border-sky-800 dark:bg-sky-950" role="status">
            <div className="flex justify-between text-sm"><span>{job.stage}</span><span>{Math.round(job.progress * 100)}%</span></div>
            <div className="h-2 overflow-hidden rounded bg-sky-100 dark:bg-sky-900"><div className="h-full bg-sky-600 transition-all" style={{ width: `${job.progress * 100}%` }} /></div>
            {job.total_frames && <p className="text-xs text-slate-600 dark:text-slate-300">{job.frames_processed.toLocaleString()} / {job.total_frames.toLocaleString()} frames</p>}
          </div>
        )}
        {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
        <p className="text-sm text-slate-500">Film from the side with the whole dive visible. Analysis begins after the recording ends.</p>
      </section>

      <aside className="space-y-6">
        <div className="space-y-3">
          <h2 className="font-semibold">Dive</h2>
          <div className="grid grid-cols-2 gap-3 text-sm">
            <label>Direction<select className={selectClass} value={setup.direction} onChange={(event) => update("direction", event.target.value as DiveSetup["direction"])}><option value="forward">Forward</option><option value="back">Back</option><option value="reverse">Reverse</option><option value="inward">Inward</option></select></label>
            <label>Position<select className={selectClass} value={setup.position} onChange={(event) => update("position", event.target.value as DiveSetup["position"])}><option value="straight">Straight</option><option value="pike">Pike</option><option value="tuck">Tuck</option><option value="free">Free</option></select></label>
            <label>Somersaults<select className={selectClass} value={setup.somersaults} onChange={(event) => update("somersaults", Number(event.target.value))}>{[0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5].map((number) => <option key={number} value={number}>{number === 0 ? "None" : number}</option>)}</select></label>
          </div>
          <p className="text-xs text-slate-500">Configured for a 1 m springboard.</p>
        </div>
        {mode === "upload" && <div className="space-y-2">
          <h2 className="font-semibold">Calibration</h2>
          <p className="text-sm text-slate-500">Mark the board tip and the visible boundary where air meets water.</p>
          <div className="flex flex-wrap gap-2 text-sm">
            <button onClick={() => setTapMode(tapMode === "board" ? null : "board")} className={`rounded-md border px-3 py-1.5 ${tapMode === "board" ? "border-amber-500 bg-amber-50 dark:bg-amber-950" : "border-slate-300 dark:border-slate-700"}`}>{calibration.board_tip ? "Board tip ✓" : "Mark board tip"}</button>
            <button onClick={() => setTapMode(tapMode === "water" ? null : "water")} className={`rounded-md border px-3 py-1.5 ${tapMode === "water" ? "border-blue-500 bg-blue-50 dark:bg-blue-950" : "border-slate-300 dark:border-slate-700"}`}>{calibration.water_y !== null ? "Water surface ✓" : "Mark water surface"}</button>
            <button className="px-2 py-1.5 text-slate-500 hover:underline" onClick={() => setCalibration({ board_tip: null, water_y: null, roi: null })}>Clear</button>
          </div>
          {tapMode && <p className="text-sm text-amber-600">Now click the {tapMode === "board" ? "end of the board" : "actual air–water boundary"} in the video.</p>}
        </div>}
      </aside>
    </div>
  );
}
