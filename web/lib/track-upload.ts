import type { PoseLandmarker, PoseLandmarkerResult } from "@mediapipe/tasks-vision";
import type { Point, PoseFrame } from "./api";
import { bounds, bridgeShortGaps, concealSubmergedJoints, hasReachedWater, mapRecoveredPose, quality, selectPose, shortEntryFallback, type Pose } from "./upload-track-math";

export type TrackingResult = { frames: PoseFrame[]; width: number; height: number; recovered: number; coverage: number };
export type TrackingProgress = { fraction: number; count: number; time: number; pose: Pose | null; canvas: HTMLCanvasElement | OffscreenCanvas };

const poses = (result: PoseLandmarkerResult): Pose[] => result.landmarks.map((pose) =>
  pose.map((p) => [p.x, p.y, p.z, p.visibility ?? 0]),
);

/** A new detector per upload keeps tracker state and timestamps out of live capture and other clips. */
export async function trackUpload(file: File, signal: AbortSignal, onProgress: (progress: TrackingProgress) => void, seed: Point | null, waterY: number | null = null): Promise<TrackingResult> {
  const { Input, BlobSource, ALL_FORMATS, CanvasSink } = await import("mediabunny");
  const { FilesetResolver, PoseLandmarker } = await import("@mediapipe/tasks-vision");
  const input = new Input({ source: new BlobSource(file), formats: ALL_FORMATS });
  let tracker: PoseLandmarker | undefined;
  let recovery: PoseLandmarker | undefined;
  try {
    const track = await input.getPrimaryVideoTrack();
    if (!track || !(await track.canDecode())) throw new Error("This browser cannot decode this video. Try Chrome or Edge, or export the clip as H.264 MP4.");
    const duration = await track.computeDuration();
    if (!Number.isFinite(duration) || duration <= 0) throw new Error("The clip has no readable duration.");
    signal.throwIfAborted();
    const fileset = await FilesetResolver.forVisionTasks("/mediapipe/wasm");
    const create = async (runningMode: "VIDEO" | "IMAGE") => {
      const options = {
        runningMode, numPoses: 2,
        // Multiple-pose mode disables MediaPipe's single-person temporal smoothing.
        // Association below selects one diver; measured joints retain their frame's positions.
        minPoseDetectionConfidence: 0.4, minPosePresenceConfidence: 0.4, minTrackingConfidence: 0.6,
      };
      try {
        return await PoseLandmarker.createFromOptions(fileset, { ...options, baseOptions: { modelAssetPath: "/models/pose_landmarker_heavy.task", delegate: "GPU" } });
      } catch {
        signal.throwIfAborted();
        return PoseLandmarker.createFromOptions(fileset, { ...options, baseOptions: { modelAssetPath: "/models/pose_landmarker_heavy.task", delegate: "CPU" } });
      }
    };
    tracker = await create("VIDEO");
    signal.throwIfAborted();
    const sink = new CanvasSink(track, { poolSize: 1 });
    const retryCanvas = document.createElement("canvas");
    retryCanvas.width = retryCanvas.height = 512;
    const ctx = retryCanvas.getContext("2d")!;
    const frames: PoseFrame[] = [];
    let previous: Pose | null = null;
    let lastSeen = -Infinity;
    let entryStarted = false;
    let lastAboveWaterPose: Pose | null = null;
    let lastAboveWaterTime = -Infinity;
    let recovered = 0, width = 0, height = 0;
    // Decode sequentially in presentation order: no playback frame drops, no invented 60 FPS samples.
    for await (const frame of sink.canvases()) {
      signal.throwIfAborted();
      const time = Math.max(0, frame.timestamp);
      if (frames.length && time <= frames[frames.length - 1].t) continue;
      if (frames.length >= 20_000) throw new Error("This clip exceeds 20,000 video frames. Trim it around the dive and try again.");
      width = frame.canvas.width; height = frame.canvas.height;
      if (width > 8192 || height > 8192) throw new Error("Please export the video at 8K resolution or lower.");
      // Keep the identity anchor through an occlusion. Recovery can search the full
      // image after a longer loss, but must still match the last observed diver.
      const reference = previous;
      // The seed is for initial acquisition only; a diver can move anywhere during flight.
      const anchor = previous ? null : seed;
      let pose = selectPose(poses(tracker.detectForVideo(frame.canvas, time * 1000)), reference, anchor);
      const uncertainLeg = !pose || [25, 26, 27, 28].some((i) => pose![i][3] < 0.5);
      if (quality(pose) < 0.7 || uncertainLeg) {
        const initialPose = pose;
        recovery ??= await create("IMAGE");
        const box = time - lastSeen <= 0.3 && reference ? bounds(reference) : null;
        const side = box ? Math.max(box.w * width, box.h * height, Math.min(width, height) * 0.18) * 2.2 : Math.max(width, height);
        const crop = {
          x: box ? (box.x + box.w / 2) * width - side / 2 : (width - side) / 2,
          y: box ? (box.y + box.h / 2) * height - side / 2 : (height - side) / 2,
          side,
        };
        // Independently re-detect the current image, enlarged and rotated, to recover
        // compact or inverted poses. Coordinates are transformed back before selection.
        for (const turn of [0, 1, 2, 3]) {
          signal.throwIfAborted();
          ctx.resetTransform(); ctx.fillStyle = "black"; ctx.fillRect(0, 0, 512, 512);
          ctx.translate(256, 256); ctx.rotate(turn * Math.PI / 2);
          ctx.scale(512 / side, 512 / side);
          ctx.drawImage(frame.canvas, -crop.x - side / 2, -crop.y - side / 2);
          const candidate = selectPose(poses(recovery.detect(retryCanvas)).map((p) => mapRecoveredPose(p, crop, turn, width, height)), reference, anchor);
          if (candidate && quality(candidate) > quality(pose) + 0.03) {
            pose = candidate;
          }
          if (quality(pose) > 0.85 && pose && [25, 26, 27, 28].every((i) => pose![i][3] >= 0.6)) break;
          // Let cancel, progress, and painting run between costly recovery passes.
          await new Promise((resolve) => setTimeout(resolve, 0));
        }
        if (pose && pose !== initialPose) recovered++;
      }
      if (pose && quality(pose) >= 0.5) { previous = pose; lastSeen = time; }
      if (waterY !== null) {
        entryStarted ||= hasReachedWater(pose, waterY) || hasReachedWater(previous, waterY);
        if (entryStarted && pose) {
          pose = concealSubmergedJoints(pose, waterY);
          if (quality(pose) > 0) { lastAboveWaterPose = pose; lastAboveWaterTime = time; }
        } else if (entryStarted && time - lastAboveWaterTime <= 0.08) {
          pose = shortEntryFallback(lastAboveWaterPose, waterY);
        }
      }
      frames.push({ t: time, lm: pose });
      onProgress({ fraction: Math.min(0.99, (time + frame.duration) / duration), count: frames.length, time, pose, canvas: frame.canvas });
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
    signal.throwIfAborted();
    if (!frames.length) throw new Error("No video frames could be decoded.");
    return { frames: bridgeShortGaps(frames), width, height, recovered, coverage: frames.filter((f) => quality(f.lm) >= 0.5).length / frames.length };
  } finally {
    tracker?.close(); recovery?.close(); input.dispose();
  }
}
