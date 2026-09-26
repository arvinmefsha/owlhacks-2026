import type { Point, PoseFrame } from "./api";

export type Pose = number[][];
export const BODY_JOINTS = [11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32];
export const MAX_BRIDGE_SECONDS = 0.08;
const WATER_ENTRY_JOINTS = [23, 24, 25, 26, 27, 28, 29, 30, 31, 32];
const ABOVE_WATER_JOINTS = [0, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22];

export function quality(pose: Pose | null): number {
  return pose ? BODY_JOINTS.reduce((sum, i) => sum + (pose[i]?.[3] ?? 0), 0) / BODY_JOINTS.length : 0;
}

/** Detect entry from the lower body reaching the calibrated water surface. */
export function hasReachedWater(pose: Pose | null, waterY: number): boolean {
  return Boolean(pose && WATER_ENTRY_JOINTS.some((i) => pose[i]?.[3] >= 0.35 && pose[i][1] >= waterY - 0.015));
}

/** Keep only measured joints that are still physically above the waterline. */
export function concealSubmergedJoints(pose: Pose, waterY: number): Pose {
  return pose.map(([x, y, z, visibility]) => [x, y, z, y >= waterY ? 0 : visibility]);
}

/** A tiny, explicitly uncertain holdover avoids a one-frame splash flicker. */
export function shortEntryFallback(previous: Pose | null, waterY: number): Pose | null {
  if (!previous) return null;
  const fallback = previous.map(([x, y, z, visibility]) => [x, y, z, y < waterY ? Math.min(0.29, visibility * 0.29) : 0]);
  return ABOVE_WATER_JOINTS.some((i) => fallback[i][3] > 0) ? fallback : null;
}

export function bounds(pose: Pose) {
  const points = pose.filter((p) => p[3] >= 0.5);
  if (points.length < 4) return null;
  const xs = points.map((p) => p[0]);
  const ys = points.map((p) => p[1]);
  return { x: Math.min(...xs), y: Math.min(...ys), w: Math.max(...xs) - Math.min(...xs), h: Math.max(...ys) - Math.min(...ys) };
}

/** Spatial association prevents choosing a different person just because result order changes. */
export function selectPose(candidates: Pose[], previous: Pose | null, seed: Point | null = null): Pose | null {
  const box = previous && bounds(previous);
  const target = box ? { x: box.x + box.w / 2, y: box.y + box.h / 2 } : seed;
  let best: Pose | null = null;
  let bestScore = -Infinity;
  for (const pose of candidates) {
    if (pose.length !== 33 || pose.some((p) => p.length !== 4 || !p.every(Number.isFinite))) continue;
    const b = bounds(pose);
    if (!b || quality(pose) < 0.3) continue;
    const distance = target ? Math.hypot(b.x + b.w / 2 - target.x, b.y + b.h / 2 - target.y) : 0;
    if (target && distance > Math.max(0.18, box ? Math.hypot(box.w, box.h) * 1.5 : 0.35)) continue;
    const score = quality(pose) - distance * 2 + (target ? 0 : Math.min(0.2, b.w * b.h));
    if (score > bestScore) { bestScore = score; best = pose; }
  }
  return best;
}

/** Undo a clockwise recovery-canvas rotation before mapping back to full-image coordinates. */
export function mapRecoveredPose(pose: Pose, crop: { x: number; y: number; side: number }, turn: number, width: number, height: number): Pose {
  return pose.map(([x, y, z, visibility]) => {
    const angle = -turn * Math.PI / 2;
    const u = (x - 0.5) * Math.cos(angle) - (y - 0.5) * Math.sin(angle) + 0.5;
    const v = (x - 0.5) * Math.sin(angle) + (y - 0.5) * Math.cos(angle) + 0.5;
    return [(crop.x + u * crop.side) / width, (crop.y + v * crop.side) / height, z * crop.side / width, visibility];
  });
}

/** Offline, two-sided gap repair. Never extrapolate, smooth measured joints, or inflate confidence. */
export function bridgeShortGaps(input: PoseFrame[]): PoseFrame[] {
  const frames = input.map((frame) => ({ t: frame.t, lm: frame.lm?.map((p) => [...p]) ?? null }));
  for (let joint = 0; joint < 33; joint++) {
    let left = -1;
    for (let right = 0; right < input.length; right++) {
      const end = input[right].lm?.[joint];
      if (!end || end[3] < 0.5) continue;
      if (left >= 0 && right > left + 1) {
        const start = input[left].lm![joint];
        const span = input[right].t - input[left].t;
        // A large discontinuity can indicate an identity swap, not an occlusion.
        if (span <= MAX_BRIDGE_SECONDS + 1e-6 && Math.hypot(end[0] - start[0], end[1] - start[1]) <= 0.18) {
          for (let i = left + 1; i < right; i++) {
            const alpha = (input[i].t - input[left].t) / span;
            frames[i].lm ??= Array.from({ length: 33 }, () => [0, 0, 0, 0]);
            // <0.3 deliberately keeps estimated joints out of the API's measured joint analysis.
            frames[i].lm![joint] = [0, 1, 2].map((axis) => start[axis] + (end[axis] - start[axis]) * alpha).concat(0.29);
          }
        }
      }
      left = right;
    }
  }
  return frames;
}

/** The pose belonging to the displayed source frame, not the next frame in time. */
export function frameIndexAt(times: number[], time: number): number {
  let lo = 0, hi = times.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (times[mid] <= time + 0.00001) lo = mid + 1;
    else hi = mid;
  }
  const index = lo - 1;
  return index >= 0 && time - times[index] <= 0.15 ? index : -1;
}
