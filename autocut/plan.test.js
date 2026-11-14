const test = require("node:test");
const assert = require("node:assert/strict");
const { buildPlan, moveBoundary, splitAt, statsFrom } = require("./static/plan.js");

function envelopeFrom(duration, hop, regions) {
  const envelope = [];
  for (let t = 0; t < duration - hop / 2; t += hop) {
    const hit = regions.find((region) => t >= region.start && t < region.end);
    envelope.push(hit ? hit.db : -70);
  }
  return envelope;
}

const silenceSettings = {
  mode: "silence",
  thresholdDb: -36,
  minSilence: 0.3,
  pad: 0,
  minKeep: 0.2,
  target: 6,
  clip: 2,
  sceneThreshold: 0.3,
  dropQuiet: false,
};

test("silence mode keeps the loud phrase and drops the long gaps", () => {
  const duration = 6;
  const hop = 0.05;
  const analysis = {
    duration,
    hop,
    hasAudio: true,
    envelope: envelopeFrom(duration, hop, [{ start: 1, end: 2.5, db: -12 }]),
    scenes: [],
  };
  const plan = buildPlan(analysis, silenceSettings);
  assert.equal(plan.ranges.length, 1);
  assert.ok(Math.abs(plan.ranges[0].start - 1) < 0.06);
  assert.ok(Math.abs(plan.ranges[0].end - 2.5) < 0.06);
  assert.ok(plan.saved > 3);
  const covered = plan.segments.reduce((sum, segment) => sum + (segment.end - segment.start), 0);
  assert.ok(Math.abs(covered - duration) < 0.02);
});

test("padding pulls a little silence back in and merges a short breath", () => {
  const duration = 5;
  const hop = 0.05;
  const analysis = {
    duration,
    hop,
    hasAudio: true,
    envelope: envelopeFrom(duration, hop, [
      { start: 0.5, end: 1.4, db: -12 },
      { start: 1.55, end: 2.4, db: -12 },
    ]),
    scenes: [],
  };
  const plan = buildPlan(analysis, { ...silenceSettings, pad: 0.1, minSilence: 0.3 });
  assert.equal(plan.ranges.length, 1);
  assert.ok(plan.ranges[0].start < 0.5);
  assert.ok(plan.ranges[0].end > 2.4);
});

test("highlight mode prefers the louder window", () => {
  const duration = 10;
  const hop = 0.05;
  const analysis = {
    duration,
    hop,
    hasAudio: true,
    envelope: envelopeFrom(duration, hop, [
      { start: 1, end: 3, db: -24 },
      { start: 6, end: 8.2, db: -8 },
    ]),
    scenes: [],
  };
  const plan = buildPlan(analysis, {
    ...silenceSettings,
    mode: "highlight",
    target: 2.2,
    clip: 2,
  });
  assert.equal(plan.ranges.length, 1);
  assert.ok(plan.ranges[0].start > 4);
  assert.ok(plan.output <= 2.4);
  assert.ok(plan.output >= 1.8);
});

test("scene mode splits on strong cuts and can drop a quiet shot", () => {
  const duration = 9;
  const hop = 0.05;
  const analysis = {
    duration,
    hop,
    hasAudio: true,
    envelope: envelopeFrom(duration, hop, [{ start: 0.2, end: 2.8, db: -12 }]),
    scenes: [
      { t: 3, score: 0.8 },
      { t: 6, score: 0.1 },
    ],
  };
  const all = buildPlan(analysis, { ...silenceSettings, mode: "scene", sceneThreshold: 0.3 });
  assert.equal(all.segments.length, 2);
  assert.ok(all.segments.every((segment) => segment.keep));
  const dropped = buildPlan(analysis, {
    ...silenceSettings,
    mode: "scene",
    sceneThreshold: 0.3,
    dropQuiet: true,
  });
  assert.equal(dropped.ranges.length, 1);
  assert.ok(dropped.ranges[0].end <= 3.05);
});

test("trim and split keep the timeline covered", () => {
  const segments = [
    { start: 0, end: 2, keep: true },
    { start: 2, end: 5, keep: false },
  ];
  const trimmed = moveBoundary(segments, 0, 3, 5);
  assert.ok(Math.abs(trimmed[0].end - 3) < 0.001);
  assert.equal(trimmed[0].end, trimmed[1].start);
  assert.ok(Math.abs(statsFrom(trimmed, 5).source - 5) < 0.001);
  const split = splitAt(trimmed, 1, 5);
  assert.equal(split.length, 3);
  assert.equal(splitAt([{ start: 0, end: 5, keep: true }], 0.02, 5), null);
});
