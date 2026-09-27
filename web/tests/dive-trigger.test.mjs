import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

async function load(file) {
  const { outputText } = ts.transpileModule(readFileSync(new URL(file, import.meta.url), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  });
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
}
const { TRIGGER_CONFIG, initialTriggerState, stepTrigger } = await load("../lib/dive-trigger.ts");

// Board tip at y = 0.4 and water at y = 0.9, so the board band is 0.12 * 0.5 = ±0.06.
const CALIBRATION = { board_tip: { x: 0.3, y: 0.4 }, water_y: 0.9 };
const ON_BOARD = 0.42;
const ABOVE_BOARD = 0.25;
const MID_AIR = 0.7;
const UNDERWATER = 0.95;

function pose(leftAnkleY, leftConfidence = 0.9, rightAnkleY = leftAnkleY, rightConfidence = leftConfidence) {
  return Array.from({ length: 17 }, (_, index) =>
    index === 15 ? [0.5, leftAnkleY, leftConfidence] : index === 16 ? [0.52, rightAnkleY, rightConfidence] : [0.5, 0.2, 0.9]);
}

/** Samples from `from` to `to` inclusive, `step` seconds apart. */
function every(from, to, keypoints, step = 0.2) {
  const count = Math.round((to - from) / step);
  return Array.from({ length: count + 1 }, (_, index) => [Math.round((from + index * step) * 1e6) / 1e6, keypoints]);
}

function run(samples, state = initialTriggerState()) {
  const events = [];
  for (const [t, keypoints] of samples) {
    const step = stepTrigger(state, { t, keypoints }, CALIBRATION);
    state = step.state;
    if (step.event) events.push([step.event, t]);
  }
  return { state, events };
}

const STARTED = every(0, 0.4, pose(ON_BOARD));

test("keeps the tuning constants in one config", () => {
  assert.deepEqual(TRIGGER_CONFIG, {
    sampleIntervalMs: 200, startHoldS: 0.3, boardBandFraction: 0.12, minAnkleConfidence: 0.35, lostAfterS: 0.6, graceS: 1.0, maxDiveS: 20,
  });
});

test("starts once the feet have stayed on the board for the hold time", () => {
  const early = run(every(0, 0.2, pose(ON_BOARD), 0.1));
  assert.deepEqual(early.events, []);
  assert.equal(early.state.phase, "waiting");
  const { state, events } = run(every(0, 0.3, pose(ON_BOARD), 0.1));
  assert.deepEqual(events, [["start", 0.3]]);
  assert.equal(state.phase, "diving");
});

test("an interrupted hold does not start a dive", () => {
  const { state, events } = run([
    [0, pose(ON_BOARD)], [0.2, pose(ON_BOARD)], [0.25, null],
    [0.3, pose(ON_BOARD)], [0.5, pose(ON_BOARD)], [0.55, pose(MID_AIR)],
    [0.6, pose(ON_BOARD)], [0.8, pose(ON_BOARD)],
  ]);
  assert.deepEqual(events, []);
  assert.equal(state.phase, "waiting");
  assert.deepEqual(run([[1.0, pose(ON_BOARD)]], state).events, [["start", 1.0]]);
});

test("ignores low-confidence ankles", () => {
  assert.deepEqual(run(every(0, 2, pose(ON_BOARD, 0.2))).events, []);
  assert.deepEqual(run(every(0, 0.4, pose(ON_BOARD, 0.9, UNDERWATER, 0.2))).events, [["start", 0.4]]);
});

test("uses the lower of two confident ankles", () => {
  assert.deepEqual(run(every(0, 2, pose(ON_BOARD, 0.9, MID_AIR, 0.9))).events, []);
});

test("ends after the feet cross the water line, but only after the grace period", () => {
  const entered = run([[0.6, pose(ABOVE_BOARD)], [0.8, pose(MID_AIR)], [1.0, pose(UNDERWATER)], ...every(1.2, 1.8, null)], run(STARTED).state);
  assert.deepEqual(entered.events, []);
  assert.equal(entered.state.phase, "grace");
  const { state, events } = run([[2.0, null]], entered.state);
  assert.deepEqual(events, [["end", 2.0]]);
  assert.equal(state.phase, "waiting");
});

test("ends when the diver disappears after leaving the board", () => {
  const leaving = run([[0.6, pose(MID_AIR)], [0.8, null], [1.0, null]], run(STARTED).state);
  assert.deepEqual(leaving.events, []);
  assert.equal(leaving.state.phase, "diving");
  const lost = run([[1.2, null]], leaving.state);
  assert.equal(lost.state.phase, "grace");
  assert.deepEqual(lost.events, []);
  assert.deepEqual(run(every(1.4, 2.2, null), lost.state).events, [["end", 2.2]]);
});

test("leaving the band upward also counts as leaving the board", () => {
  const { state, events } = run([[0.6, pose(ABOVE_BOARD)], ...every(0.8, 1.2, null)], run(STARTED).state);
  assert.deepEqual(events, []);
  assert.equal(state.phase, "grace");
});

test("does not end on disappearance while the diver never left the board band", () => {
  const { state, events } = run([...every(0.6, 1.0, pose(ON_BOARD)), ...every(1.2, 10, null)], run(STARTED).state);
  assert.deepEqual(events, []);
  assert.equal(state.phase, "diving");
});

test("discards a dive with no end 20 s after it started", () => {
  const waiting = run(every(0.6, 20.2, pose(ON_BOARD)), run(STARTED).state);
  assert.deepEqual(waiting.events, []);
  const { state, events } = run([[20.4, null]], waiting.state);
  assert.deepEqual(events, [["discard", 20.4]]);
  assert.equal(state.phase, "waiting");
});

test("returns to waiting after an end or a discard and can start the next dive", () => {
  const ended = run([...STARTED, [0.6, pose(MID_AIR)], [0.8, pose(UNDERWATER)], [1.8, null], ...every(3, 3.4, pose(ON_BOARD))]);
  assert.deepEqual(ended.events, [["start", 0.4], ["end", 1.8], ["start", 3.4]]);
  const discarded = run(every(0, 21, pose(ON_BOARD)));
  assert.deepEqual(discarded.events, [["start", 0.4], ["discard", 20.4], ["start", 21]]);
  assert.equal(discarded.state.phase, "diving");
});

test("a hurdle bounce above the board does not end the dive", () => {
  const bounced = run([
    [0.6, pose(ABOVE_BOARD)], [0.8, null], [1.0, pose(ABOVE_BOARD)],
    [1.2, pose(ON_BOARD)], [1.4, pose(ON_BOARD)], [1.6, pose(ABOVE_BOARD)], [1.8, pose(MID_AIR)],
  ], run(STARTED).state);
  assert.deepEqual(bounced.events, []);
  assert.equal(bounced.state.phase, "diving");
  assert.deepEqual(run([[2.0, pose(UNDERWATER)], [3.0, null]], bounced.state).events, [["end", 3.0]]);
});

test("does nothing until both calibration references are set", () => {
  let state = initialTriggerState();
  for (const [t, keypoints] of every(0, 2, pose(ON_BOARD))) {
    const step = stepTrigger(state, { t, keypoints }, { board_tip: { x: 0.3, y: 0.4 }, water_y: null });
    assert.equal(step.event, null);
    state = step.state;
  }
});
