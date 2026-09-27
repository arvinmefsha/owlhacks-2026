import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const { outputText } = ts.transpileModule(readFileSync(new URL("../lib/pose-drawing.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
});
const { drawPose } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);

test("waterline clips circles/strokes and intersects a crossing limb correctly", () => {
  const calls = [];
  const ctx = new Proxy({ canvas: { width: 1000, height: 1000 } }, {
    get(target, key) { return key in target ? target[key] : (...args) => calls.push([key, ...args]); },
  });
  const pose = Array.from({length:17}, () => [0,0,0,0]);
  pose[5] = [.2,.4,0,.9]; pose[7] = [.4,.6,0,.9]; pose[9] = [.5,.7,0,.9];
  drawPose(ctx, pose, .5);
  assert.ok(calls.some(c => c[0] === "rect" && c[4] === 500));
  assert.ok(calls.findIndex(c => c[0] === "clip") < calls.findIndex(c => c[0] === "stroke"));
  const endpoints = calls.filter(c => c[0] === "lineTo");
  assert.equal(endpoints.length, 1);
  assert.ok(Math.abs(endpoints[0][1] - 300) < 1e-8);
  assert.equal(endpoints[0][2], 500);
  assert.equal(calls.filter(c => c[0] === "arc").length, 1);
  assert.equal(calls.at(-1)[0], "restore");
});
