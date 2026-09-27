"use client";

import { useEffect, useEffectEvent, useRef, useState } from "react";

import { api, ApiError, type AnalysisJob, type Calibration, type Dive, type DiveSetup, type LivePose, type LiveTip, type Point } from "@/lib/api";
import { initialTriggerState, stepTrigger, TRIGGER_CONFIG, type TriggerEvent } from "@/lib/dive-trigger";
import { speakTip, stopSpeech } from "@/lib/live-speech";
import { drawCalibration, drawPose } from "@/lib/pose-drawing";
import { CoachCard } from "./CoachCard";
import { SessionTimeline } from "./SessionTimeline";
import { SkeletonReplay } from "./SkeletonReplay";

export type LiveDive = {
  number: number;
  status: "analyzing" | "complete" | "failed";
  stage: string;
  progress: number;
  diveId: string | null;
  dive: Dive | null;
  tip: string | null;
  tipSource: LiveTip["source"] | null;
  tipError: string;
  error: string;
};

type Phase = "idle" | "starting" | "notice" | "calibrating" | "walkaway" | "armed" | "summary";
type CalibrationStep = "board" | "water" | "review";
/** One run of the camera. Polling for its dives outlives the camera and ends when a new session starts or the page closes. */
type Session = { live: boolean; controller: AbortController; nextNumber: number; completed: Map<number, string> };
type Recording = { recorder: MediaRecorder; chunks: Blob[]; number: number };

const NOTICE_MS = 2000;
const WALK_AWAY_S = 5;
const POSE_MAX_WIDTH = 480;
const MIN_WATER_GAP = 0.03;
// The analysis rejects a marker exactly on the right or bottom edge as outside the frame.
const MAX_TAP = 0.999;
const POLL_MS = 1000;
const RETRY_MS = 1000;
const RECORDING_TYPES = ["video/mp4", "video/webm;codecs=vp9", "video/webm"];
const EMPTY_CALIBRATION: Calibration = { board_tip: null, water_y: null, roi: null };
const CAMERA_PHASES: Phase[] = ["starting", "notice", "calibrating", "walkaway", "armed"];
const PROMPTS: Record<CalibrationStep, string> = {
  board: "Tap the end of the diving board",
  water: "Tap the water surface",
  review: "Check that both markers line up",
};

const focusRing = "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-600";
const primaryButton = `min-h-11 rounded-lg bg-sky-700 px-4 py-2 text-sm font-semibold text-white hover:bg-sky-800 disabled:opacity-40 ${focusRing}`;
const secondaryButton = `min-h-11 rounded-lg border border-slate-300 px-4 py-2 text-sm font-semibold hover:bg-slate-100 dark:border-slate-600 dark:hover:bg-slate-800 ${focusRing}`;
const stopButton = `min-h-11 rounded-lg border border-red-600 px-4 py-2 text-sm font-semibold text-red-700 hover:bg-red-50 dark:text-red-300 dark:hover:bg-red-950 ${focusRing}`;

export function LiveSession({ setup, onActiveChange }: { setup: DiveSetup; onActiveChange?: (active: boolean) => void }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const overlayRef = useRef<HTMLCanvasElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const sessionRef = useRef<Session | null>(null);
  const recordingRef = useRef<Recording | null>(null);
  const setupRef = useRef(setup);
  const [phase, setPhase] = useState<Phase>("idle");
  const [aspect, setAspect] = useState("16 / 9");
  const [calibration, setCalibration] = useState<Calibration>(EMPTY_CALIBRATION);
  const [calibrationStep, setCalibrationStep] = useState<CalibrationStep>("board");
  const [calibrationError, setCalibrationError] = useState("");
  const [countdown, setCountdown] = useState(WALK_AWAY_S);
  const [recordingNumber, setRecordingNumber] = useState<number | null>(null);
  const [warning, setWarning] = useState("");
  const [error, setError] = useState("");
  const [dives, setDives] = useState<LiveDive[]>([]);
  const [replayNumber, setReplayNumber] = useState<number | null>(null);
  const cameraOn = CAMERA_PHASES.includes(phase);

  useEffect(() => {
    setupRef.current = setup;
  }, [setup]);

  useEffect(() => {
    onActiveChange?.(cameraOn);
  }, [cameraOn, onActiveChange]);

  // Nobody touches the laptop during a session; without this the display sleeps and the browser throttles detection.
  useEffect(() => {
    if (!cameraOn || !("wakeLock" in navigator)) return;
    let lock: WakeLockSentinel | null = null;
    let released = false;
    const request = async () => {
      if (released || document.visibilityState !== "visible") return;
      try {
        lock = await navigator.wakeLock.request("screen");
        if (released) void lock.release();
      } catch {
        lock = null;
      }
    };
    const onVisibility = () => void request();
    void request();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      released = true;
      document.removeEventListener("visibilitychange", onVisibility);
      void lock?.release();
    };
  }, [cameraOn]);

  useEffect(() => () => {
    const session = sessionRef.current;
    if (session) {
      session.live = false;
      session.controller.abort();
    }
    discardRecording(recordingRef.current);
    recordingRef.current = null;
    stopStream(streamRef.current);
    streamRef.current = null;
    stopSpeech();
  }, []);

  useEffect(() => {
    if (phase !== "notice") return;
    const timer = setTimeout(() => setPhase("calibrating"), NOTICE_MS);
    return () => clearTimeout(timer);
  }, [phase]);

  useEffect(() => {
    if (phase !== "walkaway") return;
    let remaining = WALK_AWAY_S;
    const timer = setInterval(() => {
      remaining -= 1;
      if (remaining > 0) setCountdown(remaining);
      else setPhase("armed");
    }, 1000);
    return () => clearInterval(timer);
  }, [phase]);

  useEffect(() => {
    paintOverlay(overlayRef.current, videoRef.current, calibration, null);
  }, [calibration]);

  const onSample = useEffectEvent((event: TriggerEvent) => {
    if (event === "start") startRecording();
    if (event === "end") finishRecording(true);
    if (event === "discard") finishRecording(false);
  });

  const onDetectionUnavailable = useEffectEvent((message: string) => {
    stop();
    setError(message);
  });

  useEffect(() => {
    if (phase !== "armed") return;
    const video = videoRef.current;
    if (!video) return;
    const controller = new AbortController();
    const frameCanvas = document.createElement("canvas");
    let trigger = initialTriggerState();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const pause = (ms: number) => new Promise<void>((resolve) => {
      timer = setTimeout(resolve, ms);
    });
    void (async () => {
      while (!controller.signal.aborted) {
        const grabbedAt = performance.now();
        const frame = await captureFrame(video, frameCanvas);
        if (controller.signal.aborted) return;
        let keypoints: LivePose["keypoints"] = null;
        let failed = false;
        try {
          if (!frame) throw new Error("the camera picture is not ready yet");
          const form = new FormData();
          form.append("frame", frame, "frame.jpg");
          const pose = await api<LivePose>("/live/pose", { method: "POST", body: form, signal: controller.signal });
          keypoints = pose.person ? pose.keypoints : null;
          setWarning("");
        } catch (caught) {
          if (controller.signal.aborted) return;
          if (caught instanceof ApiError && caught.status === 503) {
            onDetectionUnavailable(caught.message);
            return;
          }
          failed = true;
          setWarning(`Diver detection paused: ${(caught as Error).message.replace(/\.$/, "")}. Retrying…`);
        }
        if (controller.signal.aborted) return;
        // A failed check still advances the trigger as "nobody seen", so a dive in progress can end or time out.
        const step = stepTrigger(trigger, { t: grabbedAt / 1000, keypoints }, calibration);
        trigger = step.state;
        onSample(step.event);
        paintOverlay(overlayRef.current, video, calibration, keypoints);
        await pause(failed ? RETRY_MS : Math.max(0, TRIGGER_CONFIG.sampleIntervalMs - (performance.now() - grabbedAt)));
      }
    })();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [phase, calibration]);

  async function start() {
    const previous = sessionRef.current;
    if (previous) {
      previous.live = false;
      previous.controller.abort();
    }
    const session: Session = { live: true, controller: new AbortController(), nextNumber: 1, completed: new Map() };
    sessionRef.current = session;
    setDives([]);
    setReplayNumber(null);
    setRecordingNumber(null);
    setWarning("");
    setError("");
    setCalibration(EMPTY_CALIBRATION);
    setCalibrationStep("board");
    setCalibrationError("");
    const unavailable = !navigator.mediaDevices?.getUserMedia
      ? "The camera needs a secure page. Open this app on localhost or over https."
      : typeof MediaRecorder === "undefined"
        ? "This browser cannot record video, so live sessions are not available here."
        : "";
    if (unavailable) {
      session.live = false;
      setPhase("idle");
      setError(unavailable);
      return;
    }
    setPhase("starting");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false });
      if (!session.live) {
        stopStream(stream);
        return;
      }
      streamRef.current = stream;
      // "ended" never fires for our own track.stop(), only when the camera goes away underneath us.
      stream.getVideoTracks()[0]?.addEventListener("ended", () => {
        if (sessionRef.current !== session || !session.live) return;
        stop();
        setError("The camera stopped (it was unplugged or another app took it), so the session ended.");
      });
      const video = videoRef.current;
      if (!video) throw new Error("the preview is not ready.");
      video.srcObject = stream;
      await video.play();
      if (!session.live) return;
      setAspect(`${video.videoWidth || 16} / ${video.videoHeight || 9}`);
      setPhase("notice");
    } catch (caught) {
      if (!session.live) return;
      session.live = false;
      releaseCamera();
      setPhase("idle");
      setError(cameraMessage(caught));
    }
  }

  function stop() {
    const session = sessionRef.current;
    if (session) session.live = false;
    discardRecording(recordingRef.current);
    recordingRef.current = null;
    releaseCamera();
    stopSpeech();
    setRecordingNumber(null);
    setWarning("");
    setPhase("summary");
  }

  function releaseCamera() {
    stopStream(streamRef.current);
    streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
  }

  function tap(event: React.MouseEvent<HTMLButtonElement>) {
    if (event.detail === 0) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const point: Point = { x: clampTap((event.clientX - rect.left) / rect.width), y: clampTap((event.clientY - rect.top) / rect.height) };
    if (calibrationStep === "board") {
      setCalibration({ board_tip: point, water_y: null, roi: null });
      setCalibrationStep("water");
      setCalibrationError("");
      return;
    }
    if (calibration.board_tip && point.y <= calibration.board_tip.y + MIN_WATER_GAP) {
      setCalibrationError("The water surface has to be below the end of the board. Tap where the air meets the water.");
      return;
    }
    setCalibration({ ...calibration, water_y: point.y });
    setCalibrationStep("review");
    setCalibrationError("");
  }

  function redoCalibration() {
    setCalibration(EMPTY_CALIBRATION);
    setCalibrationStep("board");
    setCalibrationError("");
  }

  function confirmCalibration() {
    setCountdown(WALK_AWAY_S);
    setPhase("walkaway");
  }

  function startRecording() {
    const stream = streamRef.current;
    const session = sessionRef.current;
    if (!stream || !session?.live || recordingRef.current) return;
    const mimeType = RECORDING_TYPES.find((type) => MediaRecorder.isTypeSupported(type));
    try {
      const recorder = new MediaRecorder(stream, { mimeType, videoBitsPerSecond: 6_000_000 });
      const recording: Recording = { recorder, chunks: [], number: session.nextNumber };
      recorder.ondataavailable = (event) => {
        if (event.data.size) recording.chunks.push(event.data);
      };
      recorder.start(1000);
      recordingRef.current = recording;
      setRecordingNumber(recording.number);
    } catch {
      stop();
      setError("This browser could not record the camera, so the session was stopped.");
    }
  }

  function finishRecording(keep: boolean) {
    const recording = recordingRef.current;
    const session = sessionRef.current;
    recordingRef.current = null;
    setRecordingNumber(null);
    if (!recording || !session) return;
    if (!keep) {
      discardRecording(recording);
      return;
    }
    const { recorder, chunks, number } = recording;
    const clipSetup = setupRef.current;
    const clipCalibration = calibration;
    session.nextNumber += 1;
    setDives((list) => [...list, {
      number, status: "analyzing", stage: "Uploading clip", progress: 0,
      diveId: null, dive: null, tip: null, tipSource: null, tipError: "", error: "",
    }]);
    const submit = () => {
      const type = (recorder.mimeType || "video/webm").split(";")[0];
      const clip = new Blob(chunks, { type });
      void submitClip(session, number, clip, `dive.${type === "video/mp4" ? "mp4" : "webm"}`, clipSetup, clipCalibration);
    };
    recorder.onstop = submit;
    if (recorder.state === "inactive") submit();
    else recorder.stop();
  }

  function patchDive(number: number, patch: Partial<LiveDive>) {
    setDives((list) => list.map((dive) => (dive.number === number ? { ...dive, ...patch } : dive)));
  }

  async function submitClip(session: Session, number: number, clip: Blob, filename: string, clipSetup: DiveSetup, clipCalibration: Calibration) {
    const { signal } = session.controller;
    try {
      if (!clip.size) throw new Error("The recording was empty, so this dive was not saved.");
      const form = new FormData();
      form.append("payload", new Blob([JSON.stringify({
        setup: clipSetup,
        calibration: clipCalibration,
        source: "live",
        profile: "fast",
      })], { type: "application/json" }), "payload.json");
      form.append("video", clip, filename);
      const created = await api<{ id: string }>("/analysis/jobs", { method: "POST", body: form, signal });
      while (!signal.aborted) {
        const job = await api<AnalysisJob>(`/analysis/jobs/${created.id}`, { signal });
        if (signal.aborted) return;
        if (job.status === "complete") {
          if (!job.dive_id) throw new Error("Analysis finished without saving the dive.");
          await showResult(session, number, job.dive_id);
          return;
        }
        if (job.status === "failed" || job.status === "cancelled") throw new Error(job.error || "Analysis did not complete.");
        patchDive(number, { stage: job.stage, progress: job.progress });
        await delay(POLL_MS, signal);
      }
    } catch (caught) {
      if (signal.aborted) return;
      patchDive(number, { status: "failed", error: (caught as Error).message });
    }
  }

  async function showResult(session: Session, number: number, diveId: string) {
    const { signal } = session.controller;
    patchDive(number, { diveId, stage: "Loading results", progress: 1 });
    const dive = await api<Dive>(`/dives/${diveId}`, { signal });
    if (signal.aborted) return;
    const previousDiveId = previousCompleted(session.completed, number);
    session.completed.set(number, diveId);
    patchDive(number, { status: "complete", dive });
    setReplayNumber(number);
    try {
      const tip = await api<LiveTip>("/live/tip", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dive_id: diveId, previous_dive_id: previousDiveId, dive_number: number }),
        signal,
      });
      if (signal.aborted) return;
      patchDive(number, { tip: tip.tip, tipSource: tip.source });
      if (session.live) void speakTip(tip.tip);
    } catch (caught) {
      if (signal.aborted) return;
      patchDive(number, { tipError: `No tip this time: ${(caught as Error).message}` });
    }
  }

  const analyzing = dives.filter((dive) => dive.status === "analyzing");
  const replay = phase === "armed" ? dives.find((dive) => dive.number === replayNumber) : undefined;
  const split = Boolean(replay?.dive);
  const badge = split ? "text-xs" : "text-sm";
  const status = recordingNumber !== null ? `Recording dive ${recordingNumber}` : "Waiting for diver on the board";
  const statusTone = recordingNumber !== null ? "bg-red-700/90" : "bg-black/60";

  return (
    <div className="space-y-4">
      {phase === "idle" && (
        <div className="space-y-4">
          <header className="rounded-xl bg-slate-100 p-5 dark:bg-slate-900">
            <p className="text-xs font-semibold uppercase tracking-widest text-sky-700 dark:text-sky-300">Live session</p>
            <h1 className="mt-1 text-2xl font-semibold">Dive. Hear a tip. Go again.</h1>
            <p className="mt-2 text-sm leading-relaxed text-slate-600 dark:text-slate-300">
              Set the camera where it sees the board and the water. Each dive records itself, gets analyzed, and comes back with a short spoken tip.
            </p>
          </header>
          <button className={primaryButton} onClick={() => void start()}>Start live session</button>
        </div>
      )}

      {phase === "summary" && <SessionTimeline dives={dives} onNewSession={() => void start()} />}

      {cameraOn && (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold">Live session</h1>
            <p className="text-sm text-slate-600 dark:text-slate-300">
              {dives.length} {dives.length === 1 ? "dive" : "dives"} recorded{analyzing.length > 0 && ` · ${analyzing.length} still analyzing`}
            </p>
          </div>
          <button className={stopButton} onClick={stop}>Stop session</button>
        </div>
      )}

      <div className={!cameraOn ? "hidden" : split ? "grid items-start gap-4 md:grid-cols-2" : ""}>
        {replay?.dive && (
          <div className="md:row-span-2">
            <SkeletonReplay dive={replay.dive} number={replay.number} />
          </div>
        )}
        {replay?.dive && (
          <CoachCard number={replay.number} dive={replay.dive} tip={replay.tip} tipSource={replay.tipSource} tipError={replay.tipError} />
        )}
        <div className={split ? "w-60 justify-self-end" : "w-full"}>
          <div className="relative overflow-hidden rounded-lg bg-black" style={{ aspectRatio: aspect }}>
            <video ref={videoRef} className="absolute inset-0 h-full w-full object-contain" playsInline muted />
            <canvas ref={overlayRef} className="pointer-events-none absolute inset-0 h-full w-full" aria-hidden="true" />
            {phase === "starting" && <p role="status" className="absolute inset-0 flex items-center justify-center text-sm text-white">Starting camera…</p>}
            {phase === "notice" && (
              <div className="absolute inset-0 flex items-center justify-center bg-slate-600/60 p-4">
                <p role="status" className="max-w-sm rounded-xl bg-white/95 p-5 text-center font-medium text-slate-900 shadow-lg dark:bg-slate-900/95 dark:text-white">
                  Please have the board in clear view and line up the board and water with markers
                </p>
              </div>
            )}
            {phase === "calibrating" && calibrationStep !== "review" && (
              <button type="button" aria-label={PROMPTS[calibrationStep]} onClick={tap}
                className="absolute inset-0 cursor-crosshair focus-visible:outline-4 focus-visible:outline-sky-500" />
            )}
            {phase === "calibrating" && (
              <p aria-live="polite" className="pointer-events-none absolute inset-x-0 top-0 bg-black/60 px-3 py-2 text-center text-sm font-medium text-white">
                {PROMPTS[calibrationStep]}
              </p>
            )}
            {phase === "walkaway" && (
              <div role="status" className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-black/40 p-4 text-center text-white">
                <p className="text-lg font-semibold">Walk out of view and climb the ladder when ready</p>
                <p className="text-5xl font-bold tabular-nums">{countdown}</p>
              </div>
            )}
            {phase === "armed" && (
              <div role="status" className="absolute left-2 top-2 flex max-w-[calc(100%-1rem)] flex-col items-start gap-1">
                <span className={`flex items-center gap-1.5 rounded px-2 py-1 font-medium text-white ${badge} ${statusTone}`}>
                  {recordingNumber !== null && <span className="h-2 w-2 shrink-0 animate-pulse rounded-full bg-white" />}
                  {status}
                </span>
                {warning && <span className={`rounded bg-amber-700/90 px-2 py-1 font-medium text-white ${badge}`}>{warning}</span>}
                {analyzing.length > 0 && (
                  <span className={`rounded bg-sky-800/90 px-2 py-1 font-medium text-white ${badge}`}>
                    Analyzing {analyzing.length === 1 ? "dive" : "dives"} {analyzing.map((dive) => dive.number).join(", ")}
                  </span>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {phase === "calibrating" && (
        <div className="space-y-3 rounded-xl border border-slate-200 p-4 dark:border-slate-800">
          <p className="text-sm text-slate-600 dark:text-slate-300">
            {calibrationStep === "review"
              ? "The orange circle should sit on the end of the board and the blue line on the water surface."
              : "Tap the live picture. The markers stay on screen so you can line up the camera."}
          </p>
          {calibrationError && <p role="alert" className="text-sm text-red-700 dark:text-red-300">{calibrationError}</p>}
          <div className="flex flex-wrap gap-2">
            {calibrationStep === "review" && <button className={primaryButton} onClick={confirmCalibration}>Looks good</button>}
            {calibration.board_tip && <button className={secondaryButton} onClick={redoCalibration}>Redo</button>}
          </div>
        </div>
      )}

      {error && <p role="alert" className="text-sm text-red-700 dark:text-red-300">{error}</p>}
    </div>
  );
}

function clampTap(value: number) {
  return Math.max(0, Math.min(MAX_TAP, value));
}

function stopStream(stream: MediaStream | null) {
  stream?.getTracks().forEach((track) => track.stop());
}

function discardRecording(recording: Recording | null) {
  if (!recording) return;
  const { recorder } = recording;
  recorder.ondataavailable = null;
  recorder.onstop = null;
  if (recorder.state !== "inactive") recorder.stop();
}

function delay(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve) => {
    const done = () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", done);
      resolve();
    };
    const timer = setTimeout(done, ms);
    signal.addEventListener("abort", done, { once: true });
  });
}

function previousCompleted(completed: Map<number, string>, number: number): string | null {
  let best: number | null = null;
  for (const key of completed.keys()) {
    if (key < number && (best === null || key > best)) best = key;
  }
  return best === null ? null : completed.get(best) ?? null;
}

function cameraMessage(caught: unknown) {
  const name = caught instanceof Error ? caught.name : "";
  if (name === "NotAllowedError" || name === "SecurityError") return "Camera access was blocked. Allow the camera for this site, then start the session again.";
  if (name === "NotFoundError" || name === "OverconstrainedError") return "No camera was found. Connect a camera and try again.";
  if (name === "NotReadableError") return "The camera is busy in another app. Close it there and try again.";
  return `The camera could not start: ${caught instanceof Error ? caught.message : "unknown error."}`;
}

function captureFrame(video: HTMLVideoElement, canvas: HTMLCanvasElement): Promise<Blob | null> {
  if (!video.videoWidth || !video.videoHeight) return Promise.resolve(null);
  const scale = Math.min(1, POSE_MAX_WIDTH / video.videoWidth);
  const width = Math.round(video.videoWidth * scale);
  const height = Math.round(video.videoHeight * scale);
  if (canvas.width !== width) canvas.width = width;
  if (canvas.height !== height) canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) return Promise.resolve(null);
  ctx.drawImage(video, 0, 0, width, height);
  return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.7));
}

function paintOverlay(canvas: HTMLCanvasElement | null, video: HTMLVideoElement | null, calibration: Calibration, keypoints: LivePose["keypoints"]) {
  const ctx = canvas?.getContext("2d");
  if (!canvas || !ctx) return;
  if (video?.videoWidth && video.videoHeight && (canvas.width !== video.videoWidth || canvas.height !== video.videoHeight)) {
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
  }
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  drawCalibration(ctx, calibration, { boardLine: true });
  if (keypoints) drawPose(ctx, keypoints.map(([x, y, confidence]) => [x, y, 0, confidence]), null);
}
