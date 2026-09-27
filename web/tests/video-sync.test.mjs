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
const { frameIndexAt } = await load("../lib/pose-drawing.ts");
const { synchronizeVideo } = await load("../lib/video-sync.ts");

test("VFR presentation intervals never select a future pose", () => {
  const times = [0, 1 / 60, 4 / 60, 5 / 60];
  assert.equal(frameIndexAt(times, .06), 1);
  assert.equal(frameIndexAt(times, 4 / 60), 2);
  assert.equal(frameIndexAt(times, -.1), -1);
  assert.equal(frameIndexAt([], 0), -1);
  assert.equal(frameIndexAt(times, NaN), -1);
  assert.equal(frameIndexAt([Math.fround(8.1)], 8.1), 0);
});

class FakeVideo extends EventTarget {
  currentTime = 0;
  paused = true;
  seeking = false;
  ended = false;
  readyState = 2;
  next = 0;
  callbacks = new Map();
  requestVideoFrameCallback(fn) { const id = this.next++; this.callbacks.set(id, fn); return id; }
  cancelVideoFrameCallback(id) { this.callbacks.delete(id); }
  present(time) {
    const fns = [...this.callbacks.values()]; this.callbacks.clear();
    for (const fn of fns) fn(performance.now(), { mediaTime: time });
  }
}

test("uses decoded PTS, suppresses seeks, and cancels every callback", () => {
  const video = new FakeVideo(), drawn = [];
  const stop = synchronizeVideo(video, t => drawn.push(t), () => {});
  video.currentTime = 3.1; video.paused = false; video.present(3);
  assert.equal(drawn.at(-1), 3);
  video.seeking = true; video.currentTime = 7; video.present(3.02);
  assert.equal(drawn.at(-1), 3);
  video.seeking = false; video.paused = true; video.dispatchEvent(new Event("seeked"));
  assert.equal(drawn.at(-1), 7);
  video.present(7); video.currentTime = 7.02; video.dispatchEvent(new Event("pause"));
  assert.equal(drawn.at(-1), 7);
  assert.equal(video.callbacks.size, 1);
  stop(); assert.equal(video.callbacks.size, 0);
  video.dispatchEvent(new Event("seeked")); assert.equal(drawn.at(-1), 7);
});

test("fallback runs only while playing and never draws while seeking", () => {
  const callbacks = new Map(); let id = 0;
  globalThis.requestAnimationFrame = fn => { callbacks.set(++id, fn); return id; };
  globalThis.cancelAnimationFrame = key => callbacks.delete(key);
  const video = new FakeVideo(); video.requestVideoFrameCallback = undefined;
  const drawn = [];
  const stop = synchronizeVideo(video, t => drawn.push(t), () => {});
  assert.equal(callbacks.size, 0);
  video.paused = false; video.dispatchEvent(new Event("play"));
  assert.equal(callbacks.size, 1);
  video.seeking = true; video.currentTime = 2;
  const cb = [...callbacks.values()][0]; callbacks.clear(); cb();
  assert.equal(drawn.at(-1), 0);
  video.seeking = false; video.paused = true; video.dispatchEvent(new Event("pause"));
  assert.equal(drawn.at(-1), 2); assert.equal(callbacks.size, 0);
  stop();
});
