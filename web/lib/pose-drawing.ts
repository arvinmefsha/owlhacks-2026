import type { Calibration } from "@/lib/api";

export const COCO_CONNECTIONS: [number, number][] = [
  [5, 6], [5, 7], [7, 9], [6, 8], [8, 10],
  [5, 11], [6, 12], [11, 12],
  [11, 13], [13, 15], [12, 14], [14, 16],
  [0, 5], [0, 6],
];

export function drawCalibration(ctx: CanvasRenderingContext2D, calibration: Calibration, options?: { boardLine?: boolean }) {
  const { width, height } = ctx.canvas;
  const scale = Math.max(2, width / 400);
  ctx.save();
  ctx.font = `${scale * 7}px sans-serif`;
  if (calibration.water_y !== null) {
    const y = calibration.water_y * height;
    ctx.strokeStyle = "#3b82f6";
    ctx.lineWidth = scale;
    ctx.setLineDash([scale * 6, scale * 4]);
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(width, y); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "#3b82f6";
    ctx.fillText("air–water surface", scale * 4, Math.max(scale * 8, y - scale * 3));
  }
  if (calibration.board_tip) {
    const x = calibration.board_tip.x * width;
    const y = calibration.board_tip.y * height;
    ctx.strokeStyle = "#f59e0b";
    ctx.fillStyle = "#f59e0b";
    ctx.lineWidth = scale;
    if (options?.boardLine) {
      ctx.setLineDash([scale * 6, scale * 4]);
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(width, y); ctx.stroke();
      ctx.setLineDash([]);
    }
    ctx.beginPath(); ctx.arc(x, y, scale * 4, 0, Math.PI * 2); ctx.stroke();
    ctx.fillText("board tip", x + scale * 6, y - scale * 4);
  }
  ctx.restore();
}

export function frameIndexAt(times: number[], time: number): number {
  if (!Number.isFinite(time)) return -1;
  let lo = 0, hi = times.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    // PostgreSQL REAL timestamps can round by a few microseconds. Do not select
    // the next frame halfway through a long VFR frame's presentation interval.
    if (times[mid] <= time + 0.0001) lo = mid + 1;
    else hi = mid;
  }
  return lo - 1;
}

export function drawPose(ctx: CanvasRenderingContext2D, pose: number[][] | null, waterY: number | null = null) {
  if (!pose || pose.length !== 17) return;
  const { width, height } = ctx.canvas;
  const scale = Math.max(2, width / 550);
  ctx.save(); ctx.lineWidth = scale; ctx.lineCap = "round";
  if (waterY !== null && Number.isFinite(waterY)) {
    // Clip stroke widths and node circles too, not just segment center lines.
    ctx.beginPath(); ctx.rect(0, 0, width, Math.max(0, Math.min(1, waterY)) * height); ctx.clip();
  }
  for (const [a, b] of COCO_CONNECTIONS) {
    const p = pose[a], q = pose[b];
    if (!p || !q || ![p[0], p[1], p[3], q[0], q[1], q[3]].every(Number.isFinite) || Math.min(p[3], q[3]) < 0.15) continue;
    if (waterY !== null && p[1] >= waterY && q[1] >= waterY) continue;
    const measured = Math.min(p[3], q[3]) >= 0.3;
    ctx.strokeStyle = measured ? "#22d3ee" : "#fbbf24";
    ctx.setLineDash(measured ? [] : [scale * 2, scale * 2]);
    let [ax, ay] = p, [bx, by] = q;
    if (waterY !== null && (ay >= waterY || by >= waterY)) {
      const fraction = (waterY - ay) / (by - ay);
      const ix = ax + (bx - ax) * fraction;
      if (ay >= waterY) { ax = ix; ay = waterY; } else { bx = ix; by = waterY; }
    }
    ctx.beginPath(); ctx.moveTo(ax * width, ay * height); ctx.lineTo(bx * width, by * height); ctx.stroke();
  }
  ctx.setLineDash([]);
  for (const [x, y, , confidence] of pose) {
    if (![x, y, confidence].every(Number.isFinite) || confidence < 0.15 || (waterY !== null && y >= waterY)) continue;
    ctx.beginPath(); ctx.arc(x * width, y * height, scale * 1.6, 0, Math.PI * 2);
    ctx.fillStyle = confidence >= 0.3 ? "#22d3ee" : "#fbbf24";
    ctx.fill(); ctx.strokeStyle = "#0f172a"; ctx.stroke();
  }
  ctx.restore();
}
