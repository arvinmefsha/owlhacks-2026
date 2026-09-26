export type Diver = { id: string; name: string; height_cm: number | null };

export type DiveSetup = {
  position: "straight" | "pike" | "tuck" | "free";
  direction: "forward" | "back" | "reverse" | "inward";
  somersaults: number;
  apparatus: "springboard" | "platform";
  board_height_m: number;
};

export type Point = { x: number; y: number };
export type Calibration = { board_tip: Point | null; water_y: number | null };

/** One video frame: 33 landmarks of [x, y, z, visibility] in normalized image units, or null. */
export type PoseFrame = { t: number; lm: number[][] | null };

export type Phase = "takeoff" | "flight" | "entry";

export type Metric = {
  key: string;
  label: string;
  phase: Phase;
  value: number;
  unit: string;
  target: string;
  score: number;
  fault: string | null;
  t: number | null;
};

export type Analysis = {
  phases: { takeoff: number; apex: number; entry: number; entry_method: string };
  scores: { overall: number } & Partial<Record<Phase, number>>;
  metrics: Metric[];
  faults: { id: string; title: string; phase: Phase; severity: "minor" | "major"; t: number | null }[];
  info: { key: string; label: string; value: number; unit: string }[];
  warnings: string[];
  head_first: boolean;
  rotation: { measured_deg: number | null; expected_deg: number };
  series: Record<"t" | "hip_angle" | "knee_angle" | "height_m" | "rotation_deg", (number | null)[]>;
};

export type Feedback = {
  summary: string;
  faults: { title: string; phase: Phase; timestamp_s: number | null; detail: string; severity: "minor" | "major" }[];
  cues: string[];
  workouts: { id: string; reason: string }[];
};

export type VisionReview = { summary: string; notes: { phase: Phase; note: string }[] };

export type Dive = {
  id: string;
  diver_id: string;
  diver_name: string;
  recorded_at: string;
  setup: DiveSetup;
  calibration: Calibration;
  source: "live" | "upload";
  video_width: number;
  video_height: number;
  has_video: boolean;
  overall_score: number;
  analysis: Analysis;
  feedback: Feedback;
  feedback_source: "gemini" | "rules";
  vision_review: VisionReview | null;
  readiness_hr: number | null;
  readiness_br: number | null;
  /** Landmarks are flattened: 132 numbers per frame (33 x [x, y, z, visibility]). */
  frames: { t: number[]; lm: (number[] | null)[] };
};

export type DiveSummary = {
  id: string;
  recorded_at: string;
  setup: DiveSetup;
  overall_score: number;
  top_fault: string | null;
};

export type Workout = {
  id: string;
  name: string;
  targets: string[];
  sets: number;
  reps: string;
  equipment: string;
  description: string;
};

export type Progress = {
  dives: { id: string; recorded_at: string; setup: DiveSetup; overall_score: number }[];
  daily: { bucket: string; metric: string; avg_value: number; avg_score: number | null; dives: number }[];
};

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) return body.detail.map((e: { msg: string }) => e.msg).join("; ");
  } catch {}
  return `Request failed (${response.status})`;
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, init);
  if (!response.ok) throw new Error(await errorMessage(response));
  return (response.status === 204 ? undefined : await response.json()) as T;
}

export function describeDive(setup: DiveSetup): string {
  const turns = setup.somersaults === 0 ? "jump" : `${setup.somersaults} somersault${setup.somersaults === 1 ? "" : "s"}`;
  const place = setup.apparatus === "platform" ? `${setup.board_height_m} m platform` : `${setup.board_height_m} m springboard`;
  return `${setup.direction} ${turns}, ${setup.position} · ${place}`;
}
