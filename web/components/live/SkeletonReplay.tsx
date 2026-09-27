"use client";

import { useEffect, useMemo, useRef } from "react";

import type { Dive } from "@/lib/api";
import { drawCalibration, drawPose, frameIndexAt } from "@/lib/pose-drawing";

const LOOP_PAUSE_S = 1;
// The clip starts when the diver steps onto the board, so trim the standing time and loop just the dive.
const BEFORE_TAKEOFF_S = 1;
const AFTER_ENTRY_S = 0.75;

/** The tracked skeleton of a finished dive on black, looping in real time. */
export function SkeletonReplay({ dive, number }: { dive: Dive; number: number }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const width = dive.video_width || 1280;
  const height = dive.video_height || 720;
  const frames = useMemo(() => dive.frames.t.map((t, index) => {
    const flat = dive.frames.lm[index];
    return { t, lm: flat ? Array.from({ length: 17 }, (_, i) => flat.slice(i * 4, i * 4 + 4)) : null };
  }), [dive.frames]);
  const tracked = frames.some((frame) => frame.lm !== null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d", { alpha: false });
    if (!canvas || !ctx) return;
    canvas.width = width;
    canvas.height = height;
    const times = frames.map((frame) => frame.t);
    const { takeoff, entry } = dive.analysis.phases;
    let first = times[0] ?? 0;
    let last = times[times.length - 1] ?? 0;
    if (Number.isFinite(takeoff) && Number.isFinite(entry) && entry > takeoff) {
      first = Math.max(first, takeoff - BEFORE_TAKEOFF_S);
      last = Math.min(last, entry + AFTER_ENTRY_S);
    }
    const cycle = last - first + LOOP_PAUSE_S;
    let startedAt: number | null = null;
    let painted = -2;
    let animation = requestAnimationFrame(function tick(now) {
      startedAt ??= now;
      const clipTime = Math.min(last, first + (((now - startedAt) / 1000) % cycle));
      const index = frameIndexAt(times, clipTime);
      if (index !== painted) {
        painted = index;
        ctx.fillStyle = "#000000";
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        drawCalibration(ctx, dive.calibration, { boardLine: true });
        drawPose(ctx, frames[index]?.lm ?? null, dive.calibration.water_y);
      }
      animation = requestAnimationFrame(tick);
    });
    return () => cancelAnimationFrame(animation);
  }, [frames, width, height, dive.calibration, dive.analysis.phases]);

  return (
    <section className="overflow-hidden rounded-xl border border-slate-300 bg-black dark:border-slate-700" aria-label={`Dive #${number} replay`}>
      <div className="relative" style={{ aspectRatio: `${width} / ${height}` }}>
        <canvas ref={canvasRef} className="absolute inset-0 h-full w-full" aria-hidden="true" />
        <p className="absolute left-3 top-3 rounded bg-black/60 px-2 py-1 text-sm font-medium text-white">Dive #{number} replay</p>
        {!tracked && <p className="absolute inset-0 flex items-center justify-center p-4 text-center text-sm text-slate-300">No skeleton was tracked for this dive.</p>}
      </div>
    </section>
  );
}
