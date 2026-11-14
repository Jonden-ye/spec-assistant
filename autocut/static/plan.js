/* Auto-cut planning from an audio envelope and scene scores.
   The browser recomputes this on every slider move, so export stays a pure cut list. */
(function (factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof window !== "undefined") window.CutPlan = api;
})(function () {
  function round3(n) {
    return Math.round(n * 1000) / 1000;
  }

  function clamp(n, lo, hi) {
    return Math.max(lo, Math.min(hi, n));
  }

  function lin(db) {
    if (!Number.isFinite(db) || db <= -80) return 0;
    return Math.pow(10, db / 20);
  }

  function tc(seconds) {
    let t = Number.isFinite(seconds) ? Math.max(0, seconds) : 0;
    const h = Math.floor(t / 3600);
    t -= h * 3600;
    const m = Math.floor(t / 60);
    const s = t - m * 60;
    const body = `${String(m).padStart(2, "0")}:${s.toFixed(2).padStart(5, "0")}`;
    return h > 0 ? `${h}:${body}` : body;
  }

  function mergeRanges(ranges, gap) {
    const sorted = ranges
      .filter((range) => range.end - range.start > 0.001)
      .map((range) => ({ start: range.start, end: range.end }))
      .sort((a, b) => a.start - b.start);
    const out = [];
    for (const range of sorted) {
      const last = out[out.length - 1];
      if (last && range.start <= last.end + gap) last.end = Math.max(last.end, range.end);
      else out.push({ start: range.start, end: range.end });
    }
    return out;
  }

  function paint(segments, duration) {
    const out = [];
    let cursor = 0;
    for (const segment of segments) {
      const end = Math.min(duration, Math.max(segment.end, cursor));
      if (end - cursor < 0.001) continue;
      out.push({ start: cursor, end, keep: !!segment.keep });
      cursor = end;
    }
    if (duration - cursor > 0.001) {
      if (out.length) out[out.length - 1].end = duration;
      else out.push({ start: 0, end: duration, keep: true });
    }
    return out
      .map((segment) => ({
        start: round3(segment.start),
        end: round3(segment.end),
        keep: segment.keep,
      }))
      .filter((segment) => segment.end - segment.start > 0.001);
  }

  function invert(keeps, duration) {
    const segments = [];
    let cursor = 0;
    for (const keep of keeps) {
      const start = clamp(keep.start, 0, duration);
      const end = clamp(keep.end, 0, duration);
      if (end - start <= 0.0005) continue;
      if (start > cursor + 0.0005) segments.push({ start: cursor, end: start, keep: false });
      segments.push({ start, end, keep: true });
      cursor = end;
    }
    if (duration - cursor > 0.0005) segments.push({ start: cursor, end: duration, keep: false });
    return paint(segments.length ? segments : [{ start: 0, end: duration, keep: true }], duration);
  }

  function silencesFromEnvelope(envelope, hop, duration, thresholdDb, minSilence) {
    const silences = [];
    let run = null;
    for (let i = 0; i <= envelope.length; i++) {
      const t = Math.min(duration, i * hop);
      const silent = i < envelope.length && envelope[i] <= thresholdDb;
      if (silent) {
        if (run === null) run = t;
      } else if (run !== null) {
        if (t - run >= minSilence - 1e-6) silences.push({ start: run, end: t });
        run = null;
      }
    }
    return silences;
  }

  function speechFromSilences(silences, duration) {
    const speech = [];
    let cursor = 0;
    for (const silence of silences) {
      if (silence.start > cursor + 0.001) speech.push({ start: cursor, end: silence.start });
      cursor = Math.max(cursor, silence.end);
    }
    if (cursor < duration - 0.001) speech.push({ start: cursor, end: duration });
    return speech;
  }

  function buildSilenceKeeps(analysis, settings) {
    const duration = analysis.duration;
    if (!analysis.hasAudio || !analysis.envelope.length) return [{ start: 0, end: duration }];
    const silences = silencesFromEnvelope(
      analysis.envelope,
      analysis.hop,
      duration,
      settings.thresholdDb,
      settings.minSilence
    );
    let speech = speechFromSilences(silences, duration).map((range) => ({
      start: Math.max(0, range.start - settings.pad),
      end: Math.min(duration, range.end + settings.pad),
    }));
    speech = mergeRanges(speech, 0.02).filter((range) => range.end - range.start >= settings.minKeep - 1e-6);
    return speech.length ? speech : [{ start: 0, end: duration }];
  }

  function buildHighlightKeeps(analysis, settings) {
    const duration = analysis.duration;
    const target = Math.min(settings.target, duration);
    if (!analysis.hasAudio || !analysis.envelope.length || target >= duration - 0.05) {
      return [{ start: 0, end: duration }];
    }
    const hop = analysis.hop;
    const envelope = analysis.envelope;
    const clip = clamp(settings.clip, hop * 2, duration);
    const windowHops = Math.max(1, Math.round(clip / hop));
    const step = Math.max(1, Math.round(0.2 / hop));
    const candidates = [];
    const last = Math.max(0, envelope.length - windowHops);
    for (let i = 0; i <= last; i += step) {
      let acc = 0;
      for (let j = 0; j < windowHops; j++) acc += lin(envelope[i + j] ?? -90);
      candidates.push({
        start: i * hop,
        end: Math.min(duration, (i + windowHops) * hop),
        score: acc / windowHops,
      });
    }
    candidates.sort((a, b) => b.score - a.score);
    const picked = [];
    let total = 0;
    const overlaps = (a, b) => a.start < b.end - 0.05 && b.start < a.end - 0.05;
    for (const candidate of candidates) {
      // Leave a short remainder on the table instead of gluing on a sliver.
      if (total >= target - 0.4) break;
      if (picked.some((item) => overlaps(item, candidate))) continue;
      const room = target - total;
      let end = candidate.end;
      if (end - candidate.start > room) end = candidate.start + room;
      if (end - candidate.start < 0.4) continue;
      picked.push({ start: candidate.start, end });
      total += end - candidate.start;
    }
    picked.sort((a, b) => a.start - b.start);
    if (!picked.length) return [{ start: 0, end: Math.min(duration, Math.max(target, hop)) }];
    return mergeRanges(picked, 0.08);
  }

  function sceneCuts(scenes, threshold, duration) {
    const times = (scenes || [])
      .filter((scene) => scene.score >= threshold && scene.t > 0.2 && scene.t < duration - 0.2)
      .map((scene) => scene.t)
      .sort((a, b) => a - b);
    const cuts = [];
    for (const time of times) {
      if (!cuts.length || time - cuts[cuts.length - 1] >= 0.45) cuts.push(time);
    }
    return cuts;
  }

  function meanDb(envelope, hop, start, end) {
    if (!envelope.length) return -90;
    const i0 = Math.max(0, Math.floor(start / hop));
    const i1 = Math.min(envelope.length, Math.ceil(end / hop));
    if (i1 <= i0) return -90;
    let acc = 0;
    for (let i = i0; i < i1; i++) acc += lin(envelope[i]);
    const mean = acc / (i1 - i0);
    if (mean <= 0) return -90;
    return 20 * Math.log10(mean);
  }

  function buildSceneSegments(analysis, settings) {
    const duration = analysis.duration;
    const cuts = sceneCuts(analysis.scenes, settings.sceneThreshold, duration);
    const bounds = [0, ...cuts, duration];
    const segments = [];
    for (let i = 0; i < bounds.length - 1; i++) {
      const start = bounds[i];
      const end = bounds[i + 1];
      if (end - start < 0.12 && segments.length) {
        segments[segments.length - 1].end = end;
        continue;
      }
      segments.push({ start, end, keep: true });
    }
    if (!segments.length) return paint([{ start: 0, end: duration, keep: true }], duration);
    if (settings.dropQuiet && analysis.hasAudio) {
      for (const segment of segments) {
        const level = meanDb(analysis.envelope, analysis.hop, segment.start, segment.end);
        segment.keep = level > settings.thresholdDb;
      }
      if (segments.every((segment) => !segment.keep)) {
        let best = 0;
        let bestDb = -Infinity;
        segments.forEach((segment, index) => {
          const level = meanDb(analysis.envelope, analysis.hop, segment.start, segment.end);
          if (level > bestDb) {
            bestDb = level;
            best = index;
          }
        });
        segments[best].keep = true;
      }
    }
    return paint(segments, duration);
  }

  function keptRanges(segments) {
    return mergeRanges(
      segments.filter((segment) => segment.keep).map((segment) => ({ start: segment.start, end: segment.end })),
      0.03
    ).map((range) => ({ start: round3(range.start), end: round3(range.end) }));
  }

  function statsFrom(segments, duration) {
    const ranges = keptRanges(segments);
    const output = ranges.reduce((sum, range) => sum + (range.end - range.start), 0);
    return {
      source: round3(duration),
      output: round3(output),
      removed: segments.filter((segment) => !segment.keep).length,
      saved: round3(Math.max(0, duration - output)),
      ranges,
    };
  }

  function buildPlan(analysis, settings) {
    const duration = analysis.duration;
    let segments;
    if (settings.mode === "highlight") segments = invert(buildHighlightKeeps(analysis, settings), duration);
    else if (settings.mode === "scene") segments = buildSceneSegments(analysis, settings);
    else segments = invert(buildSilenceKeeps(analysis, settings), duration);
    return { segments, ...statsFrom(segments, duration) };
  }

  function moveBoundary(segments, index, time, duration) {
    if (index < 0 || index >= segments.length - 1) return segments;
    const minLen = 0.12;
    const left = segments[index];
    const right = segments[index + 1];
    const nextTime = Math.min(right.end - minLen, Math.max(left.start + minLen, time));
    const next = segments.map((segment) => ({ ...segment }));
    next[index].end = nextTime;
    next[index + 1].start = nextTime;
    return paint(next, duration);
  }

  function splitAt(segments, time, duration) {
    const index = segments.findIndex((segment) => time > segment.start + 0.08 && time < segment.end - 0.08);
    if (index < 0) return null;
    const segment = segments[index];
    const next = segments.map((item) => ({ ...item }));
    next.splice(
      index,
      1,
      { start: segment.start, end: time, keep: segment.keep },
      { start: time, end: segment.end, keep: segment.keep }
    );
    return paint(next, duration);
  }

  return {
    buildPlan,
    statsFrom,
    keptRanges,
    moveBoundary,
    splitAt,
    silencesFromEnvelope,
    tc,
    round3,
  };
});
