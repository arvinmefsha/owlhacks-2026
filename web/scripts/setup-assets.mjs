// Copies the MediaPipe wasm runtime into public/ and downloads the pose models once,
// so the app serves everything itself instead of loading from a CDN at runtime.
import { access, cp, mkdir, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const MODEL_BASE = "https://storage.googleapis.com/mediapipe-models/pose_landmarker";
const MODELS = ["full", "heavy"];

await cp(join(root, "node_modules/@mediapipe/tasks-vision/wasm"), join(root, "public/mediapipe/wasm"), {
  recursive: true,
});

await mkdir(join(root, "public/models"), { recursive: true });
for (const model of MODELS) {
  const file = join(root, "public/models", `pose_landmarker_${model}.task`);
  try {
    await access(file);
    continue;
  } catch {}
  const url = `${MODEL_BASE}/pose_landmarker_${model}/float16/latest/pose_landmarker_${model}.task`;
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Downloading ${url} failed: ${response.status}`);
  await writeFile(file, Buffer.from(await response.arrayBuffer()));
  console.log(`Downloaded pose_landmarker_${model}.task`);
}
