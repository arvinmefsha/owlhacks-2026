export type DiveSetup = {
  position: "straight" | "pike" | "tuck" | "free";
  direction: "forward" | "back" | "reverse" | "inward";
  somersaults: number;
  apparatus: "springboard" | "platform";
  board_height_m: number;
};

export type Point = { x: number; y: number };
export type Calibration = {
  board_tip: Point | null;
  water_y: number | null;
  roi?: { x: number; y: number; width: number; height: number } | null;
};

/** One video frame: 17 COCO landmarks of [x, y, z, confidence], or null. */
export type PoseFrame = { t: number; lm: number[][] | null };

export type AnalysisJob = {
  id: string;
  status: "queued" | "running" | "complete" | "failed" | "cancelled";
  stage: string;
  progress: number;
  frames_processed: number;
  total_frames: number | null;
  dive_id: string | null;
  error: string | null;
  profile: "fast" | "quality";
};

export type Phase = "takeoff" | "flight" | "entry";

export type Metric = {
  key: string;
  label: string;
  phase: Phase;
  value: number | null;
  unit: string;
  target: string;
  score: number | null;
  status?: "unavailable" | "not_applicable" | "uncertain" | "borderline" | "within_target" | "flagged";
  reason?: string | null;
  fault: string | null;
  t: number | null;
};

export type Analysis = {
  method?: string;
  phases: { takeoff: number | null; apex: number | null; entry: number | null; entry_method: string };
  scores: { overall: number | null } & Partial<Record<Phase, number>>;
  metrics: Metric[];
  faults: { id: string; title: string; phase: Phase; severity: "minor" | "major"; t: number | null }[];
  info: { key: string; label: string; value: number; unit: string }[];
  warnings: string[];
  head_first: boolean;
  rotation: { measured_deg: number | null; expected_deg: number };
  series: Record<string, (number | null)[]>;
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
  recorded_at: string;
  setup: DiveSetup;
  calibration: Calibration;
  source: "live" | "upload";
  video_width: number;
  video_height: number;
  has_video: boolean;
  overall_score: number | null;
  analysis: Analysis;
  feedback: Feedback;
  feedback_source: "gemini" | "rules";
  vision_review: VisionReview | null;
  readiness_hr: number | null;
  readiness_br: number | null;
  /** Landmarks are flattened: 68 numbers per frame (17 x [x, y, z, confidence]). */
  frames: { t: number[]; lm: (number[] | null)[] };
};

export type DiveSummary = {
  id: string;
  recorded_at: string;
  setup: DiveSetup;
  overall_score: number | null;
  analysis_method: string | null;
  top_fault: string | null;
  markers: { strengths: string[]; focus: string[]; observations: number };
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
  dives: DiveSummary[];
  daily: { bucket: string; metric: string; avg_value: number; avg_score: number | null; dives: number }[];
};

/** POST /live/pose: the single most confident person, normalized to the frame. */
export type LivePose = { person: boolean; keypoints: [number, number, number][] | null; box: [number, number, number, number] | null; inference_ms: number };
export type LiveTip = { tip: string; source: "gemini" | "rules" };

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

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
  if (!response.ok) throw new ApiError(await errorMessage(response), response.status);
  return (response.status === 204 ? undefined : await response.json()) as T;
}

export function describeDive(setup: DiveSetup): string {
  const turns = setup.somersaults === 0 ? "jump" : `${setup.somersaults} somersault${setup.somersaults === 1 ? "" : "s"}`;
  const place = setup.apparatus === "platform" ? `${setup.board_height_m} m platform` : `${setup.board_height_m} m springboard`;
  return `${setup.direction} ${turns}, ${setup.position} · ${place}`;
}
