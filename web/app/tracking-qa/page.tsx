"use client";
import { useState } from "react";
import { UploadVideoPlayer } from "@/components/UploadVideoPlayer";
import { trackUpload } from "@/lib/track-upload";
import type { PoseFrame } from "@/lib/api";
export default function QA() {
  const [status, setStatus] = useState("Ready");
  const [src, setSrc] = useState("");
  const [frames, setFrames] = useState<PoseFrame[]>([]);
  async function run() {
    try {
      setStatus("Generating two-second test clip");
      const canvas = document.createElement("canvas"); canvas.width = 640; canvas.height = 360;
      const ctx = canvas.getContext("2d")!;
      const photo = new Image(); photo.src = "/tracking-qa-pose.jpg";
      await photo.decode();
      const stream = canvas.captureStream(30);
      const mimeType = ["video/mp4", "video/webm;codecs=vp8", "video/webm"].find((type) => MediaRecorder.isTypeSupported(type));
      const recorder = new MediaRecorder(stream, { mimeType });
      const chunks: Blob[] = [];
      recorder.ondataavailable = (event) => chunks.push(event.data);
      const end = new Promise<void>((resolve) => { recorder.onstop = () => resolve(); });
      recorder.start();
      for (let i = 0; i < 30; i++) {
        ctx.fillStyle = "#0f172a"; ctx.fillRect(0, 0, 640, 360); ctx.fillStyle = "white";
        ctx.save(); ctx.translate(320, 180); ctx.rotate(i / 30 * Math.PI * 2);
        const scale = 320 / Math.max(photo.width, photo.height);
        ctx.drawImage(photo, -photo.width * scale / 2, -photo.height * scale / 2, photo.width * scale, photo.height * scale); ctx.restore();
        ctx.font = "24px sans-serif"; ctx.fillText(`Rotating pose ${i + 1}`, 15, 30);
        await new Promise((resolve) => setTimeout(resolve, 70));
      }
      recorder.stop(); await end; stream.getTracks().forEach((track) => track.stop());
      const file = new File(chunks, "qa.mp4", { type: recorder.mimeType });
      setSrc(URL.createObjectURL(file));
      setStatus("Tracking generated clip");
      const result = await trackUpload(file, new AbortController().signal, (progress) => setStatus(`Tracking frame ${progress.count}`), null);
      setFrames(result.frames);
      setStatus(`PASS: ${result.frames.length} decoded frames; coverage ${result.coverage}; dimensions ${result.width} × ${result.height}`);
    } catch (e) { setStatus(`FAIL: ${e instanceof Error ? e.message : e}`); }
  }
  return <div className="space-y-4"><button className="rounded bg-blue-700 p-4 text-white" onClick={() => void run()}>Run generated video check</button><p role="status">{status}</p>{src && <UploadVideoPlayer src={src} frames={frames} calibration={{ board_tip: null, water_y: null }} />}</div>;
}
