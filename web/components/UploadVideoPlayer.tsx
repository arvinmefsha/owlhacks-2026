"use client";

import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import type { Calibration, Point, PoseFrame } from "@/lib/api";
import { drawCalibration, drawPose, frameIndexAt } from "@/lib/pose-drawing";

const button = "min-h-11 rounded-lg border border-slate-300 px-3 text-sm font-medium hover:bg-slate-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-600 disabled:opacity-40 dark:border-slate-600 dark:hover:bg-slate-800";

export function UploadVideoPlayer({ src, frames = EMPTY_FRAMES, calibration, videoRef: externalRef, pickLabel, onPick, marker, markers = EMPTY_MARKERS }: {
  src: string; frames?: PoseFrame[]; calibration: Calibration;
  videoRef?: RefObject<HTMLVideoElement | null>;
  pickLabel?: string; onPick?: (point: Point) => void; marker?: Point | null;
  markers?: { label: string; time: number }[];
}) {
  const localRef = useRef<HTMLVideoElement>(null);
  const videoRef = externalRef ?? localRef;
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [overlay, setOverlay] = useState(true);
  const [aspect, setAspect] = useState("16 / 9");
  const [error, setError] = useState("");
  const [point, setPoint] = useState({ x: 50, y: 50 });
  const times = useMemo(() => frames.map((f) => f.t), [frames]);

  useEffect(() => {
    const video = videoRef.current, canvas = canvasRef.current;
    if (!video || !canvas) return;
    let callback = 0;
    let displayedTime = video.currentTime;
    const draw = (timestamp: number) => {
      if (!video.videoWidth) return;
      if (canvas.width !== video.videoWidth) canvas.width = video.videoWidth;
      if (canvas.height !== video.videoHeight) canvas.height = video.videoHeight;
      const ctx = canvas.getContext("2d")!;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      drawCalibration(ctx, calibration);
      if (overlay) drawPose(ctx, frames[frameIndexAt(times, timestamp)]?.lm ?? null, calibration.water_y);
      if (marker) {
        ctx.strokeStyle = "#f472b6"; ctx.lineWidth = Math.max(2, canvas.width / 400);
        ctx.beginPath(); ctx.arc(marker.x * canvas.width, marker.y * canvas.height, canvas.width / 60, 0, Math.PI * 2); ctx.stroke();
      }
    };
    const loop: VideoFrameRequestCallback = (_now, metadata) => {
      displayedTime = metadata.mediaTime;
      draw(displayedTime); setTime(displayedTime);
      callback = video.requestVideoFrameCallback(loop);
    };
    const decoded = () => {
      // RVFC uses the actual presentation timestamp. A seeked fallback covers paused
      // frames and browsers without RVFC; never draw a requested time while seeking.
      draw(video.currentTime); setTime(video.currentTime);
    };
    const seeking = () => canvas.getContext("2d")!.clearRect(0, 0, canvas.width, canvas.height);
    if (video.requestVideoFrameCallback) callback = video.requestVideoFrameCallback(loop);
    else video.addEventListener("timeupdate", decoded);
    video.addEventListener("seeked", decoded); video.addEventListener("loadeddata", decoded);
    video.addEventListener("seeking", seeking);
    draw(displayedTime);
    return () => {
      if (callback) video.cancelVideoFrameCallback(callback);
      video.removeEventListener("timeupdate", decoded); video.removeEventListener("seeked", decoded);
      video.removeEventListener("loadeddata", decoded); video.removeEventListener("seeking", seeking);
    };
  }, [calibration, frames, times, overlay, marker, videoRef]);

  function seek(value: number) {
    const video = videoRef.current;
    if (!video || !duration) return;
    video.pause();
    video.currentTime = Math.max(0, Math.min(duration, value));
    setTime(video.currentTime);
  }
  function step(direction: number) {
    const current = videoRef.current?.currentTime ?? 0;
    const next = direction > 0 ? times.find((t) => t > current + 0.0001) : times.findLast((t) => t < current - 0.0001);
    seek(next ?? current + direction / 60);
  }
  async function toggle() {
    const video = videoRef.current;
    if (!video) return;
    try { if (video.paused) await video.play(); else video.pause(); }
    catch { setError("Playback could not start. Try another video format."); }
  }
  const index = frameIndexAt(times, time);
  return (
    <section className="overflow-hidden rounded-xl border border-slate-300 bg-white dark:border-slate-700 dark:bg-slate-950" aria-label="Dive video review">
      <div className="relative bg-black" style={{ aspectRatio: aspect }}>
        <video ref={videoRef} src={src} className="absolute inset-0 h-full w-full" playsInline muted preload="auto"
          onLoadedMetadata={(e) => {
            const video = e.currentTarget;
            setDuration(Number.isFinite(video.duration) ? video.duration : 0);
            setAspect(`${video.videoWidth} / ${video.videoHeight}`);
            video.playbackRate = speed;
          }}
          onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)}
          onError={() => setError("This video could not be opened. Try an H.264 MP4 file.")}
        />
        <canvas ref={canvasRef} className="pointer-events-none absolute inset-0 h-full w-full" aria-hidden="true" />
        {onPick && <button type="button" aria-label={pickLabel ?? "Select video position"}
          className="absolute inset-0 cursor-crosshair focus-visible:outline-4 focus-visible:outline-sky-500"
          onClick={(e) => {
            if (e.detail === 0) { onPick({ x: point.x / 100, y: point.y / 100 }); return; }
            const rect = e.currentTarget.getBoundingClientRect();
            onPick({ x: Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width)), y: Math.max(0, Math.min(1, (e.clientY - rect.top) / rect.height)) });
          }} />}
      </div>
      <div className="space-y-3 p-4">
        <div className="flex items-center justify-between gap-2 text-sm">
          <span className="font-semibold">{frames.length ? "Review your tracking" : "Preview your clip"}</span>
          <output className="font-mono text-xs tabular-nums">{time.toFixed(2)} / {duration.toFixed(2)} s</output>
        </div>
        <label className="block text-sm font-medium">Video timeline
          <input type="range" min={0} max={duration || 1} step="any" value={Math.min(time, duration)} disabled={!duration}
            onChange={(e) => seek(Number(e.target.value))}
            onKeyDown={(e) => { if (e.key === "ArrowLeft" || e.key === "ArrowRight") { e.preventDefault(); step(e.key === "ArrowLeft" ? -1 : 1); } }}
            aria-valuetext={`${time.toFixed(2)} seconds${index >= 0 ? `, frame ${index + 1} of ${frames.length}` : ""}`}
            className="mt-1 block h-8 w-full cursor-pointer accent-sky-600 focus-visible:outline-2 focus-visible:outline-sky-600" />
        </label>
        <div className="flex flex-wrap items-center gap-2">
          <button className={button} disabled={!duration} onClick={() => step(-1)} aria-label="Previous tracked frame">← Frame</button>
          <button className={`${button} min-w-20 bg-sky-700 text-white hover:bg-sky-800`} disabled={!duration} onClick={() => void toggle()}>{playing ? "Pause" : "Play"}</button>
          <button className={button} disabled={!duration} onClick={() => step(1)} aria-label="Next tracked frame">Frame →</button>
          <label className="ml-auto flex items-center gap-2 text-sm">Speed
            <select className={button} value={speed} onChange={(e) => { const value = Number(e.target.value); setSpeed(value); if (videoRef.current) videoRef.current.playbackRate = value; }}>
              {[0.25, 0.5, 1].map((value) => <option key={value} value={value}>{value}×</option>)}
            </select>
          </label>
          {frames.length > 0 && <button className={button} aria-pressed={overlay} onClick={() => setOverlay(!overlay)}>{overlay ? "Hide" : "Show"} skeleton</button>}
        </div>
        {markers.length > 0 && <div className="flex flex-wrap gap-2">{markers.map((mark) => <button key={mark.label} className={button} onClick={() => seek(mark.time)}>{mark.label} · {mark.time.toFixed(2)} s</button>)}</div>}
        <p className="text-xs leading-relaxed text-slate-600 dark:text-slate-300">Drag to scrub. Use ← / → on the timeline to step through {frames.length ? "tracked frames" : "the preview"}.
          {frames.length > 0 && " Solid cyan: visible joints. Dashed amber: uncertain or briefly estimated joints."}</p>
        {onPick && <fieldset className="flex flex-wrap items-end gap-2 rounded-lg bg-slate-100 p-3 dark:bg-slate-800">
          <legend className="text-sm font-medium">{pickLabel} — click the image or enter a position</legend>
          {(["x", "y"] as const).map((axis) => <label key={axis} className="text-sm">{axis.toUpperCase()} (%)<input type="number" min={0} max={100} value={point[axis]} onChange={(e) => setPoint({ ...point, [axis]: Math.max(0, Math.min(100, Number(e.target.value))) })} className="ml-2 min-h-11 w-20 rounded border px-2 dark:bg-slate-900" /></label>)}
          <button className={button} onClick={() => onPick({ x: point.x / 100, y: point.y / 100 })}>Set position</button>
        </fieldset>}
        {error && <p role="alert" className="text-sm text-red-700 dark:text-red-300">{error}</p>}
      </div>
    </section>
  );
}
const EMPTY_FRAMES: PoseFrame[] = [];
const EMPTY_MARKERS: { label: string; time: number }[] = [];
