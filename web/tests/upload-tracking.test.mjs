import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import ts from "typescript";

// Keep these math regressions runnable without a browser or another test dependency.
const source = readFileSync(new URL("../lib/upload-track-math.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { bridgeShortGaps, concealSubmergedJoints, frameIndexAt, hasReachedWater, mapRecoveredPose, selectPose, shortEntryFallback } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
const pose = (x, visibility = 0.9) => Array.from({ length: 33 }, (_, i) => [x + (i % 2) * 0.03, 0.2 + i * 0.012, 0, visibility]);

test("short occlusions are bounded by measurements, confidence stays below the analysis threshold", () => {
  const frames = [{ t: 0, lm: pose(0.4) }, { t: 0.02, lm: null }, { t: 0.04, lm: pose(0.44) }];
  const result = bridgeShortGaps(frames);
  assert.equal(result[1].lm[0][0], 0.42000000000000004);
  assert.ok(result[1].lm.every((p) => p[3] < 0.3));
  assert.equal(frames[1].lm, null);
  assert.deepEqual(result[0], frames[0]);
  assert.deepEqual(result[2], frames[2]);
});

test("never extrapolates a missing start, ending, long gap, or identity jump", () => {
  const frames = [{ t: 0, lm: null }, { t: 0.1, lm: pose(0.2) }, { t: 0.2, lm: null }, { t: 0.4, lm: pose(0.4) }, { t: 0.5, lm: null }];
  assert.deepEqual(bridgeShortGaps(frames), frames);
  const jump = [{ t: 0, lm: pose(0.1) }, { t: 0.02, lm: null }, { t: 0.04, lm: pose(0.8) }];
  assert.equal(bridgeShortGaps(jump)[1].lm, null);
});

test("one hidden ankle is repaired without delaying any visible limb", () => {
  const middle = pose(0.42); middle[27][3] = 0.1;
  const result = bridgeShortGaps([{ t: 0, lm: pose(0.4) }, { t: 0.02, lm: middle }, { t: 0.04, lm: pose(0.44) }]);
  assert.equal(result[1].lm[27][3], 0.29);
  for (let i = 0; i < 33; i++) if (i !== 27) assert.deepEqual(result[1].lm[i], middle[i]);
});

test("presentation timestamp lookup does not show a future pose or retain stale tracking", () => {
  const times = [0, 1 / 120, 2 / 120, 0.3];
  assert.equal(frameIndexAt(times, 0.007), 0);
  assert.equal(frameIndexAt(times, 1 / 120), 1);
  assert.equal(frameIndexAt(times, 0.2), -1);
  assert.equal(frameIndexAt([], 0), -1);
  assert.equal(frameIndexAt([0.1], 0), -1);
});

test("rotation and crop recovery maps landmarks back to the correct limb position", () => {
  for (let turn = 0; turn < 4; turn++) {
    const u = 0.2, v = 0.7, a = turn * Math.PI / 2;
    const x = (u - 0.5) * Math.cos(a) - (v - 0.5) * Math.sin(a) + 0.5;
    const y = (u - 0.5) * Math.sin(a) + (v - 0.5) * Math.cos(a) + 0.5;
    const [p] = mapRecoveredPose([[x, y, 0.1, 0.9]], { x: 100, y: -20, side: 400 }, turn, 1920, 1080);
    assert.ok(Math.abs(p[0] - 180 / 1920) < 1e-10);
    assert.ok(Math.abs(p[1] - 260 / 1080) < 1e-10);
    assert.equal(p[2], 40 / 1920);
    assert.equal(p[3], 0.9);
  }
});

test("result reordering does not switch the diver to a distant bystander", () => {
  const diver = pose(0.25), bystander = pose(0.9, 0.99);
  assert.equal(selectPose([bystander, diver], pose(0.24)), diver);
  assert.equal(selectPose([diver, bystander], pose(0.24)), diver);
  assert.equal(selectPose([bystander], pose(0.1)), null);
  assert.equal(selectPose([bystander, diver], null, { x: 0.26, y: 0.4 }), diver);
});

test("water entry keeps only the body above the marked surface and flags it as entry", () => {
  const diver = pose(0.35);
  diver[11][1] = 0.42; // shoulder above water
  diver[15][1] = 0.5; // wrist above water
  diver[23][1] = 0.59; // hip at the water
  diver[27][1] = 0.72; // ankle under water
  assert.equal(hasReachedWater(diver, 0.58), true);
  const visible = concealSubmergedJoints(diver, 0.58);
  assert.equal(visible[11][3], 0.9);
  assert.equal(visible[15][3], 0.9);
  assert.equal(visible[23][3], 0);
  assert.equal(visible[27][3], 0);
});

test("entry fallback is brief and visibly uncertain, never presenting submerged joints as measured", () => {
  const diver = pose(0.35);
  diver[11][1] = 0.42;
  diver[27][1] = 0.72;
  const fallback = shortEntryFallback(diver, 0.58);
  assert.ok(fallback[11][3] > 0 && fallback[11][3] < 0.3);
  assert.equal(fallback[27][3], 0);
  assert.equal(shortEntryFallback(null, 0.58), null);
});
