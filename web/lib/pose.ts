import type { PoseLandmarker, PoseLandmarkerResult } from "@mediapipe/tasks-vision";

import type { Calibration } from "@/lib/api";

export type PoseModel = "full" | "heavy";

const landmarkers = new Map<PoseModel, Promise<PoseLandmarker>>();

/** A VIDEO-mode pose landmarker, created once per model. "full" is fast enough for live video; "heavy" is more accurate for uploads. */
export function getPoseLandmarker(model: PoseModel): Promise<PoseLandmarker> {
  let landmarker = landmarkers.get(model);
  if (!landmarker) {
    landmarker = (async () => {
      const { FilesetResolver, PoseLandmarker } = await import("@mediapipe/tasks-vision");
      const fileset = await FilesetResolver.forVisionTasks("/mediapipe/wasm");
      return PoseLandmarker.createFromOptions(fileset, {
        baseOptions: { modelAssetPath: `/models/pose_landmarker_${model}.task`, delegate: "GPU" },
        runningMode: "VIDEO",
        numPoses: 1,
      });
    })();
    landmarkers.set(model, landmarker);
  }
  return landmarker;
}

const round = (v: number) => Math.round(v * 1e5) / 1e5;

function toFrameLandmarks(result: PoseLandmarkerResult): number[][] | null {
  const pose = result.landmarks[0];
  return pose ? pose.map((p) => [round(p.x), round(p.y), round(p.z), round(p.visibility ?? 0)]) : null;
}

// MediaPipe rejects a timestamp that isn't greater than the previous one for the same landmarker.
const lastTimestamp = new WeakMap<PoseLandmarker, number>();

/** Landmarks for the video's current frame: 33 x [x, y, z, visibility], or null if no one was found. */
export function detectPose(landmarker: PoseLandmarker, video: HTMLVideoElement): number[][] | null {
  const timestamp = Math.max(performance.now(), (lastTimestamp.get(landmarker) ?? 0) + 1);
  lastTimestamp.set(landmarker, timestamp);
  return toFrameLandmarks(landmarker.detectForVideo(video, timestamp));
}

/** Redraw the overlay canvas (sized to the video) with calibration marks and an optional flattened skeleton. */
export function renderOverlay(
  canvas: HTMLCanvasElement,
  video: HTMLVideoElement,
  calibration: Calibration,
  lm: number[] | null,
) {
  if (!video.videoWidth) return;
  if (canvas.width !== video.videoWidth) canvas.width = video.videoWidth;
  if (canvas.height !== video.videoHeight) canvas.height = video.videoHeight;
  const ctx = canvas.getContext("2d")!;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  drawCalibration(ctx, calibration);
  if (lm) drawPose(ctx, lm);
}

export function estimateFps(times: number[]): number | null {
  if (times.length < 2) return null;
  const gaps = times.slice(1).map((t, i) => t - times[i]).filter((d) => d > 0).sort((a, b) => a - b);
  if (gaps.length === 0) return null;
  return Math.min(480, Math.round(1 / gaps[Math.floor(gaps.length / 2)]));
}

const CONNECTIONS: [number, number][] = [
  [11, 12], [11, 13], [13, 15], [12, 14], [14, 16],
  [11, 23], [12, 24], [23, 24],
  [23, 25], [25, 27], [27, 29], [29, 31], [27, 31],
  [24, 26], [26, 28], [28, 30], [30, 32], [28, 32],
];

/** Draw a skeleton from flattened landmarks (132 numbers) onto a canvas sized to the video. */
export function drawPose(ctx: CanvasRenderingContext2D, lm: number[], color = "#22d3ee") {
  const { width, height } = ctx.canvas;
  const at = (i: number) => [lm[i * 4] * width, lm[i * 4 + 1] * height, lm[i * 4 + 3]] as const;
  const scale = Math.max(2, width / 400);
  ctx.lineWidth = scale;
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  for (const [a, b] of CONNECTIONS) {
    const [ax, ay, av] = at(a);
    const [bx, by, bv] = at(b);
    if (Math.min(av, bv) < 0.3) continue;
    ctx.beginPath();
    ctx.moveTo(ax, ay);
    ctx.lineTo(bx, by);
    ctx.stroke();
  }
  for (const i of [0, 11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28, 31, 32]) {
    const [x, y, v] = at(i);
    if (v < 0.3) continue;
    ctx.beginPath();
    ctx.arc(x, y, scale * 1.5, 0, Math.PI * 2);
    ctx.fill();
  }
}

export function drawCalibration(ctx: CanvasRenderingContext2D, calibration: Calibration) {
  const { width, height } = ctx.canvas;
  const scale = Math.max(2, width / 400);
  ctx.font = `${scale * 7}px sans-serif`;
  if (calibration.water_y !== null) {
    const y = calibration.water_y * height;
    ctx.strokeStyle = "#3b82f6";
    ctx.lineWidth = scale;
    ctx.setLineDash([scale * 6, scale * 4]);
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(width, y);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "#3b82f6";
    ctx.fillText("water", scale * 4, y - scale * 3);
  }
  if (calibration.board_tip) {
    const x = calibration.board_tip.x * width;
    const y = calibration.board_tip.y * height;
    ctx.strokeStyle = "#f59e0b";
    ctx.fillStyle = "#f59e0b";
    ctx.lineWidth = scale;
    ctx.beginPath();
    ctx.arc(x, y, scale * 4, 0, Math.PI * 2);
    ctx.stroke();
    ctx.fillText("board tip", x + scale * 6, y - scale * 4);
  }
}
