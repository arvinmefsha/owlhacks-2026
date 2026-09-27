import type { Calibration, PoseFrame } from "@/lib/api";
import { drawCalibration, drawPose } from "@/lib/pose-drawing";

export type ExportMode = "overlay" | "black";

const MIME_TYPES = ["video/mp4;codecs=avc1.42E01E", "video/mp4;codecs=h264", "video/mp4"];

function recordingType(): { mimeType: string; extension: string } {
  const mimeType = MIME_TYPES.find((type) => MediaRecorder.isTypeSupported(type));
  if (!mimeType) throw new Error("This browser cannot record an MP4 video download.");
  return { mimeType, extension: "mp4" };
}

function frameGap(times: number[], index: number): number {
  const current = times[index];
  const next = times[index + 1];
  const previous = times[index - 1];
  const gap = next !== undefined ? next - current : previous !== undefined ? current - previous : 1 / 30;
  return Math.min(0.2, Math.max(1 / 120, gap));
}

function wait(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function seekVideo(video: HTMLVideoElement, time: number) {
  const target = Math.max(0, Math.min(Number.isFinite(video.duration) ? video.duration : time, time));
  if (Math.abs(video.currentTime - target) < 0.0008 && video.readyState >= 2) return Promise.resolve();
  return new Promise<void>((resolve) => {
    video.addEventListener("seeked", () => resolve(), { once: true });
    video.currentTime = target;
  });
}

function paintFrame(
  ctx: CanvasRenderingContext2D,
  video: HTMLVideoElement,
  frame: PoseFrame | undefined,
  calibration: Calibration,
  mode: ExportMode,
) {
  const { width, height } = ctx.canvas;
  if (mode === "black") {
    ctx.fillStyle = "#000000";
    ctx.fillRect(0, 0, width, height);
    drawCalibration(ctx, calibration, { boardLine: true });
  } else {
    ctx.drawImage(video, 0, 0, width, height);
  }
  drawPose(ctx, frame?.lm ?? null, calibration.water_y);
}

/** Record tracked frames into a downloadable clip. Playback time matches the pose timestamps. */
export async function exportAnnotatedVideo(
  video: HTMLVideoElement,
  frames: PoseFrame[],
  calibration: Calibration,
  mode: ExportMode,
  onProgress: (fraction: number) => void,
): Promise<{ blob: Blob; extension: string }> {
  if (!video.videoWidth || !video.videoHeight) throw new Error("The video is still loading.");
  if (!frames.length) throw new Error("This dive has no tracked frames to export.");
  const { mimeType, extension } = recordingType();
  const canvas = document.createElement("canvas");
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  const ctx = canvas.getContext("2d", { alpha: false });
  if (!ctx) throw new Error("Could not prepare the download canvas.");
  const stream = canvas.captureStream(0);
  const track = stream.getVideoTracks()[0] as CanvasCaptureMediaStreamTrack;
  if (typeof track.requestFrame !== "function") throw new Error("This browser cannot export a canvas video.");

  const chunks: Blob[] = [];
  const recorder = new MediaRecorder(stream, { mimeType });
  recorder.ondataavailable = (event) => {
    if (event.data.size) chunks.push(event.data);
  };
  const stopped = new Promise<void>((resolve, reject) => {
    recorder.onstop = () => resolve();
    recorder.onerror = () => reject(new Error("The browser could not finish the video."));
  });
  const times = frames.map((frame) => frame.t);
  recorder.start();
  try {
    for (let index = 0; index < frames.length; index += 1) {
      const started = performance.now();
      if (mode === "overlay") await seekVideo(video, frames[index].t + 0.0002);
      paintFrame(ctx, video, frames[index], calibration, mode);
      track.requestFrame();
      onProgress((index + 1) / frames.length);
      const remaining = frameGap(times, index) * 1000 - (performance.now() - started);
      if (remaining > 0) await wait(remaining);
    }
  } finally {
    if (recorder.state !== "inactive") recorder.stop();
    track.stop();
  }
  await stopped;
  if (!chunks.length) throw new Error("The download was empty.");
  return { blob: new Blob(chunks, { type: mimeType }), extension };
}
