"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { UploadCapture } from "@/components/UploadCapture";
import { DiverPicker, useSelectedDiver } from "@/components/DiverPicker";
import { api, type Calibration, type DiveSetup, type Point, type PoseFrame } from "@/lib/api";
import { detectPose, estimateFps, getPoseLandmarker, renderOverlay } from "@/lib/pose";

const DEFAULT_SETUP: DiveSetup = {
  position: "tuck",
  direction: "forward",
  somersaults: 1.5,
  apparatus: "springboard",
  board_height_m: 1,
};

type TapMode = "board" | "water" | null;

type Capture = {
  frames: PoseFrame[];
  video: Blob;
  filename: string;
  width: number;
  height: number;
  source: "live" | "upload";
};

type CaptureProps = {
  calibration: Calibration;
  tapMode: TapMode;
  onTap: (p: Point) => void;
  onDone: (capture: Capture) => void;
  disabled: boolean;
};

const selectClass = "mt-1 block w-full rounded-md border border-slate-300 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-900";

export default function RecordPage() {
  const router = useRouter();
  const [diverId, setDiverId] = useSelectedDiver();
  const [setup, setSetup] = useState<DiveSetup>(DEFAULT_SETUP);
  const [calibration, setCalibration] = useState<Calibration>({ board_tip: null, water_y: null });
  const [tapMode, setTapMode] = useState<TapMode>(null);
  const [mode, setMode] = useState<"live" | "upload">("live");
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");

  function onTap(p: Point) {
    if (tapMode === "board") setCalibration((c) => ({ ...c, board_tip: p }));
    if (tapMode === "water") setCalibration((c) => ({ ...c, water_y: p.y }));
    setTapMode(null);
  }

  async function submit(capture: Capture) {
    if (!diverId) {
      setError("Pick or add a diver first.");
      return;
    }
    setUploading(true);
    setError("");
    const payload = {
      diver_id: diverId,
      setup,
      calibration,
      video: {
        width: capture.width,
        height: capture.height,
        fps: estimateFps(capture.frames.map((f) => f.t)),
        source: capture.source,
      },
      frames: capture.frames,
    };
    const form = new FormData();
    // Sent as a file part: plain form fields are capped at 1 MB by the API's multipart parser.
    form.append("payload", new Blob([JSON.stringify(payload)], { type: "application/json" }), "payload.json");
    form.append("video", capture.video, capture.filename);
    try {
      const dive = await api<{ id: string }>("/dives", { method: "POST", body: form });
      router.push(`/dives/${dive.id}`);
    } catch (e) {
      setError((e as Error).message);
      setUploading(false);
    }
  }

  const update = <K extends keyof DiveSetup>(key: K, value: DiveSetup[K]) => setSetup((s) => ({ ...s, [key]: value }));
  const captureProps: CaptureProps = { calibration, tapMode, onTap, onDone: submit, disabled: uploading };

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
      <section className="space-y-3">
        <div className="flex gap-2">
          {(["live", "upload"] as const).map((m) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={`rounded-md px-3 py-1.5 text-sm font-medium ${
                mode === m ? "bg-slate-900 text-white dark:bg-white dark:text-slate-900" : "bg-slate-100 dark:bg-slate-800"
              }`}
            >
              {m === "live" ? "Live camera" : "Upload video"}
            </button>
          ))}
        </div>
        {mode === "live" ? <LiveCapture {...captureProps} /> : <UploadCapture {...captureProps} />}
        {uploading && <p className="text-sm text-slate-600">Analysing the dive and writing feedback…</p>}
        {error && <p className="text-sm text-red-600">{error}</p>}
        <p className="text-sm text-slate-500">
          Film from the side, level with the board, with the whole dive from takeoff to entry in view. A tripod helps.
        </p>
      </section>

      <aside className="space-y-6">
        <div>
          <h2 className="mb-2 font-semibold">Diver</h2>
          <DiverPicker value={diverId} onChange={setDiverId} />
        </div>

        <div className="space-y-3">
          <h2 className="font-semibold">Dive</h2>
          <div className="grid grid-cols-2 gap-3 text-sm">
            <label>
              Direction
              <select className={selectClass} value={setup.direction} onChange={(e) => update("direction", e.target.value as DiveSetup["direction"])}>
                <option value="forward">Forward</option>
                <option value="back">Back</option>
                <option value="reverse">Reverse</option>
                <option value="inward">Inward</option>
              </select>
            </label>
            <label>
              Position
              <select className={selectClass} value={setup.position} onChange={(e) => update("position", e.target.value as DiveSetup["position"])}>
                <option value="straight">Straight</option>
                <option value="pike">Pike</option>
                <option value="tuck">Tuck</option>
                <option value="free">Free</option>
              </select>
            </label>
            <label>
              Somersaults
              <select className={selectClass} value={setup.somersaults} onChange={(e) => update("somersaults", Number(e.target.value))}>
                {[0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5].map((n) => (
                  <option key={n} value={n}>
                    {n === 0 ? "None (jump)" : n}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Apparatus
              <select className={selectClass} value={setup.apparatus} onChange={(e) => update("apparatus", e.target.value as DiveSetup["apparatus"])}>
                <option value="springboard">Springboard</option>
                <option value="platform">Platform</option>
              </select>
            </label>
            <label>
              Height (m)
              <input
                type="number"
                min={0.5}
                max={10}
                step={0.5}
                className={selectClass}
                value={setup.board_height_m}
                onChange={(e) => update("board_height_m", Number(e.target.value))}
              />
            </label>
          </div>
        </div>

        <div className="space-y-2">
          <h2 className="font-semibold">Calibration</h2>
          <p className="text-sm text-slate-500">
            Tap the video to mark the board tip and the water line. It makes entry timing and distances more accurate.
          </p>
          <div className="flex flex-wrap gap-2 text-sm">
            <button
              onClick={() => setTapMode(tapMode === "board" ? null : "board")}
              className={`rounded-md border px-3 py-1.5 ${tapMode === "board" ? "border-amber-500 bg-amber-50 dark:bg-amber-950" : "border-slate-300 dark:border-slate-700"}`}
            >
              {calibration.board_tip ? "Board tip ✓" : "Tap board tip"}
            </button>
            <button
              onClick={() => setTapMode(tapMode === "water" ? null : "water")}
              className={`rounded-md border px-3 py-1.5 ${tapMode === "water" ? "border-blue-500 bg-blue-50 dark:bg-blue-950" : "border-slate-300 dark:border-slate-700"}`}
            >
              {calibration.water_y !== null ? "Water line ✓" : "Tap water line"}
            </button>
            <button className="px-2 py-1.5 text-slate-500 hover:underline" onClick={() => setCalibration({ board_tip: null, water_y: null })}>
              Clear
            </button>
          </div>
          {tapMode && <p className="text-sm text-amber-600">Now tap the {tapMode === "board" ? "end of the board" : "water surface"} on the video.</p>}
        </div>
      </aside>
    </div>
  );
}

function Stage({
  videoRef,
  canvasRef,
  aspect,
  tapMode,
  onTap,
  onLoaded,
  children,
}: {
  videoRef: React.RefObject<HTMLVideoElement | null>;
  canvasRef: React.RefObject<HTMLCanvasElement | null>;
  aspect: string;
  tapMode: TapMode;
  onTap: (p: Point) => void;
  onLoaded?: () => void;
  children?: React.ReactNode;
}) {
  function tap(event: React.MouseEvent<HTMLCanvasElement>) {
    if (!tapMode) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const clamp = (v: number) => Math.min(1, Math.max(0, v));
    onTap({ x: clamp((event.clientX - rect.left) / rect.width), y: clamp((event.clientY - rect.top) / rect.height) });
  }
  return (
    <div className="relative w-full overflow-hidden rounded-lg bg-black" style={{ aspectRatio: aspect }}>
      <video ref={videoRef} onLoadedData={onLoaded} className="absolute inset-0 h-full w-full" playsInline muted preload="auto" />
      <canvas ref={canvasRef} onClick={tap} className={`absolute inset-0 h-full w-full ${tapMode ? "cursor-crosshair" : ""}`} />
      {children}
    </div>
  );
}

/** Keep calibrationRef current (the frame loop reads it) and redraw the overlay when calibration changes. */
function useCalibrationRedraw(
  videoRef: React.RefObject<HTMLVideoElement | null>,
  canvasRef: React.RefObject<HTMLCanvasElement | null>,
  calibrationRef: React.RefObject<Calibration>,
  lastPoseRef: React.RefObject<number[] | null>,
  calibration: Calibration,
) {
  useEffect(() => {
    calibrationRef.current = calibration;
    if (videoRef.current && canvasRef.current) renderOverlay(canvasRef.current, videoRef.current, calibration, lastPoseRef.current);
  }, [calibration, videoRef, canvasRef, calibrationRef, lastPoseRef]);
}

function LiveCapture({ calibration, tapMode, onTap, onDone, disabled }: CaptureProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [aspect, setAspect] = useState("16 / 9");
  const [status, setStatus] = useState("Starting camera…");
  const [recordingSince, setRecordingSince] = useState<number | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const calibrationRef = useRef(calibration);
  const lastPoseRef = useRef<number[] | null>(null);
  useCalibrationRedraw(videoRef, canvasRef, calibrationRef, lastPoseRef, calibration);
  const recording = useRef<{ recorder: MediaRecorder; chunks: Blob[]; start: number; frames: PoseFrame[] } | null>(null);

  useEffect(() => {
    const video = videoRef.current!;
    let stream: MediaStream | null = null;
    let handle = 0;
    let cancelled = false;
    (async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: "environment", width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 60 } },
          audio: false,
        });
        if (cancelled) return stream.getTracks().forEach((t) => t.stop());
        video.srcObject = stream;
        await video.play();
        setAspect(`${video.videoWidth} / ${video.videoHeight}`);
        setStatus("Loading pose model…");
        const landmarker = await getPoseLandmarker("full");
        if (cancelled) return;
        setStatus("");
        const step = (now: number) => {
          if (cancelled) return;
          const lm = detectPose(landmarker, video);
          const rec = recording.current;
          if (rec) rec.frames.push({ t: Math.max(0, (now - rec.start) / 1000), lm });
          lastPoseRef.current = lm && lm.flat();
          renderOverlay(canvasRef.current!, video, calibrationRef.current, lastPoseRef.current);
          handle = video.requestVideoFrameCallback(step);
        };
        handle = video.requestVideoFrameCallback(step);
      } catch (e) {
        setStatus(`Camera unavailable: ${(e as Error).message}`);
      }
    })();
    return () => {
      cancelled = true;
      video.cancelVideoFrameCallback(handle);
      stream?.getTracks().forEach((t) => t.stop());
    };
  }, []);

  useEffect(() => {
    if (recordingSince === null) return;
    const timer = setInterval(() => setElapsed((performance.now() - recordingSince) / 1000), 200);
    return () => clearInterval(timer);
  }, [recordingSince]);

  function start() {
    const stream = videoRef.current!.srcObject as MediaStream;
    // MP4 first: Chrome's WebM recordings have no duration, which breaks seeking on the review page.
    const mimeType = ["video/mp4", "video/webm;codecs=vp9", "video/webm"].find((m) => MediaRecorder.isTypeSupported(m));
    const recorder = new MediaRecorder(stream, { mimeType, videoBitsPerSecond: 6_000_000 });
    const rec = { recorder, chunks: [] as Blob[], start: 0, frames: [] as PoseFrame[] };
    recorder.ondataavailable = (e) => e.data.size && rec.chunks.push(e.data);
    recorder.start();
    rec.start = performance.now();
    recording.current = rec;
    setElapsed(0);
    setRecordingSince(rec.start);
  }

  function stop() {
    const rec = recording.current;
    if (!rec) return;
    recording.current = null;
    setRecordingSince(null);
    rec.recorder.onstop = () => {
      const type = (rec.recorder.mimeType || "video/webm").split(";")[0];
      const video = videoRef.current!;
      onDone({
        frames: rec.frames,
        video: new Blob(rec.chunks, { type }),
        filename: `dive.${type === "video/mp4" ? "mp4" : "webm"}`,
        width: video.videoWidth,
        height: video.videoHeight,
        source: "live",
      });
    };
    rec.recorder.stop();
  }

  const isRecording = recordingSince !== null;
  return (
    <div className="space-y-3">
      <Stage videoRef={videoRef} canvasRef={canvasRef} aspect={aspect} tapMode={tapMode} onTap={onTap}>
        {status && <div className="absolute inset-0 flex items-center justify-center text-sm text-white">{status}</div>}
        {isRecording && (
          <div className="absolute left-3 top-3 flex items-center gap-2 rounded bg-black/60 px-2 py-1 text-sm text-white">
            <span className="h-2 w-2 animate-pulse rounded-full bg-red-500" />
            {elapsed.toFixed(1)} s
          </div>
        )}
      </Stage>
      <button
        onClick={isRecording ? stop : start}
        disabled={disabled || Boolean(status)}
        className={`rounded-md px-4 py-2 font-medium text-white disabled:opacity-50 ${isRecording ? "bg-slate-800" : "bg-red-600 hover:bg-red-700"}`}
      >
        {isRecording ? "Stop and analyse" : "Start recording"}
      </button>
    </div>
  );
}
