"use client";

import { useEffect, useRef, useState } from "react";

import type { Calibration, Point } from "@/lib/api";
import { UploadVideoPlayer } from "./UploadVideoPlayer";

type Capture = { video: Blob; filename: string; source: "upload" };
const button = "min-h-11 rounded-lg border border-slate-300 px-4 py-2 text-sm font-semibold focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-600 disabled:opacity-40 dark:border-slate-600";

export function UploadCapture({ calibration, tapMode, onTap, onDone, disabled }: {
  calibration: Calibration;
  tapMode: "board" | "water" | null;
  onTap: (point: Point) => void;
  onDone: (capture: Capture) => void;
  disabled: boolean;
}) {
  const [clip, setClip] = useState<{ file: File; url: string } | null>(null);
  const [error, setError] = useState("");
  const urlRef = useRef<string | null>(null);

  useEffect(() => () => {
    if (urlRef.current) URL.revokeObjectURL(urlRef.current);
  }, []);

  function choose(file: File | undefined) {
    if (!file) return;
    if (file.size > 200 * 1024 * 1024) {
      setError("Choose a video smaller than 200 MB. Trimming it to the dive will also reduce analysis time.");
      return;
    }
    if (!/\.(mp4|mov|webm)$/i.test(file.name)) {
      setError("Choose an MP4, MOV, or WebM video.");
      return;
    }
    if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    const url = URL.createObjectURL(file);
    urlRef.current = url;
    setClip({ file, url });
    setError("");
  }

  return (
    <div className="space-y-4">
      <header className="rounded-xl bg-slate-100 p-5 dark:bg-slate-900">
        <p className="text-xs font-semibold uppercase tracking-widest text-sky-700 dark:text-sky-300">YOLO video analysis</p>
        <h1 className="mt-1 text-2xl font-semibold">Upload. Analyze. Review.</h1>
        <p className="mt-2 text-sm leading-relaxed text-slate-600 dark:text-slate-300">
          Select a clip, mark the board and visible air–water surface, then send it to the local high-accuracy tracker.
        </p>
      </header>

      <label className="block rounded-xl border-2 border-dashed border-slate-300 p-4 text-sm dark:border-slate-600">
        <span className="block font-semibold">{clip ? "Replace video" : "Choose your dive video"}</span>
        <span className="my-1 block text-slate-600 dark:text-slate-300">MP4, MOV, or WebM · up to 200 MB · side-on footage works best</span>
        <input
          type="file"
          accept="video/mp4,video/webm,video/quicktime,.mov"
          disabled={disabled}
          onChange={(event) => choose(event.target.files?.[0])}
          className="mt-2 max-w-full file:mr-3 file:min-h-11 file:rounded-lg file:border-0 file:bg-slate-200 file:px-4 file:font-semibold focus-visible:outline-2 focus-visible:outline-sky-600 dark:file:bg-slate-700 dark:file:text-white"
        />
      </label>

      {clip && (
        <>
          <UploadVideoPlayer
            src={clip.url}
            calibration={calibration}
            pickLabel={tapMode === "board" ? "Mark the board tip" : tapMode === "water" ? "Mark the visible air–water surface" : undefined}
            onPick={tapMode ? onTap : undefined}
          />
          <button
            className={`${button} border-sky-700 bg-sky-700 text-white hover:bg-sky-800`}
            disabled={disabled || calibration.board_tip === null || calibration.water_y === null}
            onClick={() => onDone({ video: clip.file, filename: clip.file.name, source: "upload" })}
          >
            Analyze with YOLO
          </button>
          {(calibration.board_tip === null || calibration.water_y === null) && (
            <p className="text-sm text-amber-700 dark:text-amber-300">Mark both calibration references before analysis.</p>
          )}
        </>
      )}
      {error && <p role="alert" className="text-sm text-red-700 dark:text-red-300">{error}</p>}
    </div>
  );
}
