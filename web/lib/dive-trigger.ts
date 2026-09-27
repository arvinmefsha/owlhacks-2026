import type { Calibration } from "@/lib/api";

/** Every threshold the live trigger uses, in one place so it can be tuned at the pool. */
export const TRIGGER_CONFIG = {
  sampleIntervalMs: 200,
  startHoldS: 0.3,
  boardBandFraction: 0.12,
  minAnkleConfidence: 0.35,
  lostAfterS: 0.6,
  graceS: 1.0,
  maxDiveS: 20,
};

export type TriggerConfig = typeof TRIGGER_CONFIG;
export type TriggerCalibration = Pick<Calibration, "board_tip" | "water_y">;
/** One pose sample: time in seconds and 17 COCO keypoints of [x, y, confidence], normalized to the frame. */
export type TriggerSample = { t: number; keypoints: [number, number, number][] | null };
export type TriggerEvent = "start" | "end" | "discard" | null;
export type TriggerState =
  | { phase: "waiting"; holdSince: number | null }
  | { phase: "diving"; startT: number; leftBoard: boolean; lastFeetT: number }
  | { phase: "grace"; graceSince: number };

const LEFT_ANKLE = 15;
const RIGHT_ANKLE = 16;
// Sample times arrive as floats; without a little slack 10.6 - 10.0 would fall short of 0.6.
const TIME_SLACK_S = 1e-6;

export function initialTriggerState(): TriggerState {
  return { phase: "waiting", holdSince: null };
}

/** The lowest confident ankle in the image (largest y), or null when neither ankle is confident. */
export function feetY(keypoints: TriggerSample["keypoints"], minConfidence: number): number | null {
  let lowest: number | null = null;
  for (const index of [LEFT_ANKLE, RIGHT_ANKLE]) {
    const ankle = keypoints?.[index];
    if (!ankle || ankle[2] < minConfidence) continue;
    if (lowest === null || ankle[1] > lowest) lowest = ankle[1];
  }
  return lowest;
}

function reached(since: number, t: number, seconds: number) {
  return t - since >= seconds - TIME_SLACK_S;
}

export function stepTrigger(
  state: TriggerState,
  sample: TriggerSample,
  calibration: TriggerCalibration,
  config: TriggerConfig = TRIGGER_CONFIG,
): { state: TriggerState; event: TriggerEvent } {
  const boardY = calibration.board_tip?.y;
  const waterY = calibration.water_y;
  if (boardY === undefined || waterY === null) return { state, event: null };
  const { t } = sample;
  const feet = feetY(sample.keypoints, config.minAnkleConfidence);
  const band = config.boardBandFraction * Math.abs(waterY - boardY);
  const onBoard = feet !== null && Math.abs(feet - boardY) <= band;

  if (state.phase === "waiting") {
    if (!onBoard) return { state: initialTriggerState(), event: null };
    const holdSince = state.holdSince ?? t;
    if (reached(holdSince, t, config.startHoldS)) {
      return { state: { phase: "diving", startT: t, leftBoard: false, lastFeetT: t }, event: "start" };
    }
    return { state: { phase: "waiting", holdSince }, event: null };
  }

  if (state.phase === "diving") {
    if (feet !== null && feet >= waterY) return { state: { phase: "grace", graceSince: t }, event: null };
    const leftBoard = state.leftBoard || (feet !== null && !onBoard);
    if (leftBoard && feet === null && reached(state.lastFeetT, t, config.lostAfterS)) {
      return { state: { phase: "grace", graceSince: t }, event: null };
    }
    if (reached(state.startT, t, config.maxDiveS)) return { state: initialTriggerState(), event: "discard" };
    return { state: { ...state, leftBoard, lastFeetT: feet === null ? state.lastFeetT : t }, event: null };
  }

  if (reached(state.graceSince, t, config.graceS)) return { state: initialTriggerState(), event: "end" };
  return { state, event: null };
}
