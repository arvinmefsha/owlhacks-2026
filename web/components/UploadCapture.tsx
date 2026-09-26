"use client";

import { useEffect, useRef, useState } from "react";
import type { Calibration, Point, PoseFrame } from "@/lib/api";
import { trackUpload, type TrackingResult } from "@/lib/track-upload";
import { drawUploadPose, UploadVideoPlayer } from "./UploadVideoPlayer";

type Capture = { frames: PoseFrame[]; video: Blob; filename: string; width: number; height: number; source: "upload" };
const button = "min-h-11 rounded-lg border border-slate-300 px-4 py-2 text-sm font-semibold focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-600 disabled:opacity-40 dark:border-slate-600";

export function UploadCapture({ calibration, tapMode, onTap, onDone, disabled }: {
  calibration: Calibration; tapMode: "board" | "water" | null; onTap: (p: Point) => void;
  onDone: (capture: Capture) => void; disabled: boolean;
}) {
  const [clip, setClip] = useState<{ file: File; url: string } | null>(null);
  const [result, setResult] = useState<TrackingResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState({ fraction: 0, count: 0 });
  const [error, setError] = useState("");
  const [seed, setSeed] = useState<Point | null>(null);
  const [selecting, setSelecting] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const urlRef = useRef<string | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const previewRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => () => {
    abortRef.current?.abort();
    abortRef.current = null;
    if (urlRef.current) URL.revokeObjectURL(urlRef.current);
  }, []);

  function choose(file: File | undefined) {
    if (!file) return;
    if (file.size > 200 * 1024 * 1024) { setError("Choose a video smaller than 200 MB. Trimming to just the dive also speeds up tracking."); return; }
    if (!/\.(mp4|mov|webm)$/i.test(file.name)) { setError("Choose an MP4, MOV, or WebM video."); return; }
    abortRef.current?.abort();
    if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    const url = URL.createObjectURL(file); urlRef.current = url;
    setClip({ file, url }); setResult(null); setSeed(null); setSelecting(false); setError("");
    setProgress({ fraction: 0, count: 0 });
  }

  async function analyse() {
    if (!clip || abortRef.current || disabled) return;
    const controller = new AbortController(); abortRef.current = controller;
    setBusy(true); setError(""); setSelecting(false); setResult(null);
    setProgress({ fraction: 0, count: 0 }); videoRef.current?.pause();
    try {
      const tracked = await trackUpload(clip.file, controller.signal, (next) => {
        const canvas = previewRef.current;
        if (canvas) {
          if (canvas.width !== next.canvas.width) canvas.width = next.canvas.width;
          if (canvas.height !== next.canvas.height) canvas.height = next.canvas.height;
          const ctx = canvas.getContext("2d")!;
          ctx.drawImage(next.canvas, 0, 0); drawUploadPose(ctx, next.pose, calibration.water_y);
        }
        setProgress({ fraction: next.fraction, count: next.count });
      }, seed, calibration.water_y);
      controller.signal.throwIfAborted();
      setResult(tracked); setProgress({ fraction: 1, count: tracked.frames.length });
      if (videoRef.current) videoRef.current.currentTime = 0;
    } catch (e) {
      if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "Tracking failed. Please try again.");
    } finally {
      if (abortRef.current === controller) { abortRef.current = null; setBusy(false); }
    }
  }
  function cancel() { abortRef.current?.abort(); }
  const locked = busy || disabled;
  return (
    <div className="space-y-4">
      <header className="rounded-xl bg-slate-100 p-5 dark:bg-slate-900">
        <p className="text-xs font-semibold uppercase tracking-widest text-sky-700 dark:text-sky-300">Video workspace</p>
        <h1 className="mt-1 text-2xl font-semibold">Track. Review. Improve.</h1>
        <p className="mt-2 text-sm leading-relaxed text-slate-600 dark:text-slate-300">Choose a clip, track the diver, then inspect every moment before saving your analysis.</p>
        <ol className="mt-4 flex flex-wrap gap-3 text-sm" aria-label="Upload steps">
          {["1 · Choose clip", "2 · Track diver", "3 · Review & save"].map((step, i) => <li key={step} aria-current={(!clip ? 0 : !result ? 1 : 2) === i ? "step" : undefined} className="rounded-full border border-slate-300 px-3 py-1 aria-[current=step]:border-sky-600 aria-[current=step]:bg-sky-700 aria-[current=step]:text-white dark:border-slate-600">{step}</li>)}
        </ol>
      </header>
      <label className="block rounded-xl border-2 border-dashed border-slate-300 p-4 text-sm dark:border-slate-600">
        <span className="block font-semibold">{clip ? "Replace video" : "Choose your dive video"}</span>
        <span className="my-1 block text-slate-600 dark:text-slate-300">MP4, MOV, or WebM · up to 200 MB · side-on footage works best</span>
        <input type="file" accept="video/mp4,video/webm,video/quicktime,.mov" disabled={locked} onChange={(e) => choose(e.target.files?.[0])}
          className="mt-2 max-w-full file:mr-3 file:min-h-11 file:rounded-lg file:border-0 file:bg-slate-200 file:px-4 file:font-semibold focus-visible:outline-2 focus-visible:outline-sky-600 dark:file:bg-slate-700 dark:file:text-white" />
      </label>
      {clip && <>
        <p className="break-all text-sm font-medium">{clip.file.name} <span className="font-normal text-slate-600 dark:text-slate-300">· {(clip.file.size / 1024 / 1024).toFixed(1)} MB</span></p>
        {calibration.water_y === null && <p className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">Mark the water line in Calibration before tracking. That lets the entry tracker keep the above-water body visible and stop drawing submerged limbs.</p>}
        <div className={busy ? "hidden" : ""}>
          <UploadVideoPlayer key={clip.url} src={clip.url} frames={result?.frames} calibration={calibration} videoRef={videoRef} marker={selecting || !result ? seed : null}
            pickLabel={tapMode ? `Mark the ${tapMode === "board" ? "board tip" : "water line"}` : "Select the diver in the first frame"}
            onPick={disabled ? undefined : tapMode ? onTap : selecting ? (p) => { setSeed(p); setSelecting(false); setResult(null); } : undefined} />
        </div>
        {busy && <div className="space-y-3 rounded-xl border border-slate-300 p-4 dark:border-slate-600" aria-busy="true">
          <canvas ref={previewRef} className="w-full rounded-lg bg-black" aria-label="Current decoded frame and pose" />
          <div className="flex items-center justify-between gap-4">
            <p role="status" className="text-sm font-medium">{progress.count ? `Tracking · ${Math.round(progress.fraction * 100)}%` : "Preparing the video tracker…"}</p>
            <button className={button} onClick={cancel}>Cancel</button>
          </div>
          <progress max={1} value={progress.fraction} className="block h-3 w-full accent-sky-600" aria-label="Tracking progress" />
          <p className="text-xs text-slate-600 dark:text-slate-300">{progress.count.toLocaleString()} source frames processed. Difficult poses can take longer.</p>
        </div>}
        {!busy && <div className="flex flex-wrap gap-2">
          <button className={`${button} bg-sky-700 text-white hover:bg-sky-800`} onClick={() => void analyse()} disabled={locked}>{result ? "Track again" : "Track diver"}</button>
          <button className={button} disabled={locked} aria-pressed={selecting} onClick={() => { videoRef.current?.pause(); if (videoRef.current) videoRef.current.currentTime = 0; setSelecting(!selecting); }}>Select diver {seed ? "✓" : "(optional)"}</button>
          {seed && <button className={button} disabled={locked} onClick={() => { setSeed(null); setResult(null); }}>Clear selection</button>}
        </div>}
        {result && <section className="space-y-3 rounded-xl border border-sky-200 bg-sky-50 p-4 dark:border-sky-800 dark:bg-sky-950">
          <h2 className="font-semibold">Ready to review</h2>
          <p className="text-sm">{result.frames.length.toLocaleString()} frames · {Math.round(result.coverage * 100)}% of frames have a clear body track · {result.recovered} frames improved by recovery.</p>
          <p className="text-sm text-slate-700 dark:text-slate-200">Scrub through tuck, pike, and entry. Amber joints are uncertain; hidden limbs and blur can still cause gaps. Original frame rate is preserved, including 60 / 120 FPS footage.</p>
          <button className={`${button} bg-sky-700 text-white hover:bg-sky-800`} disabled={disabled || result.coverage === 0}
            onClick={() => onDone({ frames: result.frames, video: clip.file, filename: clip.file.name, width: result.width, height: result.height, source: "upload" })}>
            {disabled ? "Saving and analysing…" : "Save dive & get feedback"}
          </button>
          <p className="text-xs text-slate-600 dark:text-slate-300">Choose a diver in the sidebar before saving. Tracking and preview work without saving.</p>
        </section>}
      </>}
      {error && <p role="alert" className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950 dark:text-red-200">{error}</p>}
    </div>
  );
}
