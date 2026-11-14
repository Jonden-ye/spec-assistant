(function () {
  const $ = (id) => document.getElementById(id);
  const player = $("player");
  const canvas = $("timeline");

  const state = {
    asset: null,
    analysis: null,
    settings: null,
    segments: [],
    selected: 0,
    dirty: false,
    preview: "cut",
    aspect: "original",
    normalize: true,
    token: 0,
    exporting: false,
  };

  const drag = { pointer: null, handle: null, moved: false, x: 0, index: 0 };
  let seeking = false;
  let toastTimer = 0;
  let nowIndex = -1;

  function prettyFps(fps) {
    const match = String(fps || "").match(/^(\d+(?:\.\d+)?)(?:\/(\d+))?$/);
    if (!match) return String(fps || "");
    const value = match[2] ? Number(match[1]) / Number(match[2]) : Number(match[1]);
    if (!Number.isFinite(value)) return String(fps);
    if (Math.abs(value - Math.round(value)) < 0.05) return String(Math.round(value));
    return value.toFixed(2);
  }

  function toast(message) {
    const node = $("toast");
    node.textContent = message;
    node.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      node.hidden = true;
    }, 4200);
  }

  async function readError(response) {
    try {
      const data = await response.json();
      if (typeof data.detail === "string") return data.detail;
    } catch (err) {
      /* ignore broken error bodies */
    }
    return "请求失败";
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function setVeil(fraction, label) {
    const show = label != null && label !== "";
    $("veil").hidden = !show;
    if (!show) return;
    $("veil-label").textContent = label;
    $("veil-bar").style.width = `${Math.max(4, Math.round(fraction * 100))}%`;
  }

  function setExportProgress(fraction, label) {
    const show = state.exporting;
    $("export-progress").hidden = !show;
    if (!show) return;
    $("export-label").textContent = label || "正在导出";
    $("export-bar").style.width = `${Math.max(4, Math.round((fraction || 0) * 100))}%`;
  }

  async function poll(jobId, mine, onProgress) {
    for (;;) {
      if (mine !== state.token) throw new Error("已换了一条片子");
      const response = await fetch(`/api/jobs/${jobId}`);
      if (!response.ok) throw new Error(await readError(response));
      const job = await response.json();
      if (onProgress) onProgress(job);
      if (job.status === "done") return job;
      if (job.status === "error") throw new Error(job.error || "处理失败");
      await sleep(280);
    }
  }

  function uploadFile(file) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/upload");
      xhr.responseType = "json";
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) setVeil(event.loaded / event.total, "正在上传");
      };
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) resolve(xhr.response);
        else reject(new Error((xhr.response && xhr.response.detail) || "上传失败"));
      };
      xhr.onerror = () => reject(new Error("连不上剪辑服务"));
      const body = new FormData();
      body.append("file", file);
      xhr.send(body);
    });
  }

  function settingsFor(analysis) {
    const target = Math.min(6, Math.max(2, Math.round(analysis.duration * 0.45 * 10) / 10));
    return {
      mode: analysis.hasAudio ? "silence" : "scene",
      thresholdDb: -36,
      minSilence: 0.3,
      pad: 0.06,
      minKeep: 0.28,
      target,
      clip: Math.min(2.2, Math.max(0.8, analysis.duration / 4)),
      sceneThreshold: 0.28,
      dropQuiet: false,
    };
  }

  function writeSettings() {
    const settings = state.settings;
    $("threshold").value = settings.thresholdDb;
    $("min-silence").value = settings.minSilence;
    $("pad").value = settings.pad;
    $("min-keep").value = settings.minKeep;
    $("target").max = state.analysis ? String(Math.max(2, state.analysis.duration)) : "60";
    $("target").value = settings.target;
    $("clip").value = settings.clip;
    $("scene").value = settings.sceneThreshold;
    $("drop-quiet").checked = settings.dropQuiet;
    document.querySelectorAll(".modes button").forEach((button) => {
      button.classList.toggle("on", button.dataset.mode === settings.mode);
    });
    syncModeFields();
    syncLabels();
  }

  function readSettings() {
    state.settings.thresholdDb = Number($("threshold").value);
    state.settings.minSilence = Number($("min-silence").value);
    state.settings.pad = Number($("pad").value);
    state.settings.minKeep = Number($("min-keep").value);
    state.settings.target = Number($("target").value);
    state.settings.clip = Number($("clip").value);
    state.settings.sceneThreshold = Number($("scene").value);
    state.settings.dropQuiet = $("drop-quiet").checked;
    syncLabels();
  }

  function syncLabels() {
    $("val-threshold").textContent = `${Math.round(Number($("threshold").value))} dB`;
    $("val-min-silence").textContent = `${Number($("min-silence").value).toFixed(2)} 秒`;
    $("val-pad").textContent = `${Number($("pad").value).toFixed(2)} 秒`;
    $("val-min-keep").textContent = `${Number($("min-keep").value).toFixed(2)} 秒`;
    $("val-target").textContent = `${Number($("target").value).toFixed(1)} 秒`;
    $("val-clip").textContent = `${Number($("clip").value).toFixed(1)} 秒`;
    $("val-scene").textContent = Number($("scene").value).toFixed(2);
  }

  function syncModeFields() {
    const mode = state.settings ? state.settings.mode : "silence";
    $("fields-silence").hidden = mode !== "silence";
    $("fields-highlight").hidden = mode !== "highlight";
    $("fields-scene").hidden = mode !== "scene";
  }

  function applyAutoPlan() {
    const plan = CutPlan.buildPlan(state.analysis, state.settings);
    state.segments = plan.segments;
    state.selected = Math.max(0, plan.segments.findIndex((segment) => segment.keep));
    state.dirty = false;
  }

  function hintText() {
    if (!state.analysis) return "上传之后会按模式自动出一版，时间轴上还能改。";
    const stats = CutPlan.statsFrom(state.segments, state.analysis.duration);
    if (!state.analysis.hasAudio) return "这条没有音轨。可以按镜头拆开，再手动去掉不要的。";
    if (state.settings.mode === "silence" && stats.saved < 0.25) {
      return "几乎没有低于阈值的停顿。把静音阈值往右拖，更轻的声音也会被剪掉。";
    }
    if (state.settings.mode === "highlight") return "高光按音量从高到低取片段，直到凑够目标时长。";
    if (state.settings.mode === "scene") return "每条色块是一个镜头。点「去掉」拿掉不要的，导出时按顺序接上。";
    return "绿色留下，暗色剪掉。双击色块可以改，拖两端可以修。";
  }

  function renderList() {
    const list = $("list");
    if (!state.segments.length) {
      list.replaceChildren();
      const note = document.createElement("p");
      note.className = "quiet";
      note.textContent = "剪完之后，每一段会出现在这里。点「去掉」可以把它从成片里拿掉。";
      list.appendChild(note);
      $("list-count").textContent = "";
      return;
    }
    const scroll = list.scrollTop;
    list.replaceChildren();
    state.segments.forEach((segment, index) => {
      const row = document.createElement("button");
      row.type = "button";
      row.className = `seg${segment.keep ? " keep" : " cut"}${index === state.selected ? " on" : ""}${index === nowIndex ? " now" : ""}`;
      const idx = document.createElement("span");
      idx.className = "idx";
      idx.textContent = String(index + 1).padStart(2, "0");
      const span = document.createElement("span");
      span.className = "span";
      span.textContent = `${CutPlan.tc(segment.start)} – ${CutPlan.tc(segment.end)}`;
      const len = document.createElement("span");
      len.className = "len";
      const seconds = segment.end - segment.start;
      len.textContent = seconds < 60 ? `${seconds.toFixed(1)}s` : CutPlan.tc(seconds);
      const flag = document.createElement("span");
      flag.className = "flag";
      flag.textContent = segment.keep ? "保留" : "去掉";
      row.append(idx, span, len, flag);
      row.addEventListener("click", (event) => {
        if (event.target.closest(".flag")) {
          state.selected = index;
          toggleSelected();
          return;
        }
        state.selected = index;
        player.currentTime = segment.start + 0.01;
        render();
      });
      list.appendChild(row);
    });
    list.scrollTop = scroll;
    const removed = state.segments.filter((segment) => !segment.keep).length;
    $("list-count").textContent = removed ? `去掉 ${removed}` : `${state.segments.length} 段`;
  }

  function render() {
    const ready = !!(state.asset && state.analysis);
    document.body.dataset.ready = state.asset ? "1" : "0";
    $("plan").disabled = !ready;
    $("play").disabled = !state.asset;
    $("split").disabled = !ready || state.exporting;
    $("toggle").disabled = !ready || state.exporting;
    $("reapply").disabled = !ready || state.exporting;
    $("reapply").classList.toggle("attention", state.dirty);
    document.querySelectorAll("[data-preview]").forEach((button) => {
      button.disabled = !state.asset;
      button.classList.toggle("on", button.dataset.preview === state.preview);
    });
    const stats = ready ? CutPlan.statsFrom(state.segments, state.analysis.duration) : null;
    $("export").disabled = !ready || state.exporting || !stats || stats.output < 0.2;
    $("export").textContent = state.exporting ? "正在导出" : "导出成片";
    if (stats) {
      $("totals").textContent = `原片 ${CutPlan.tc(stats.source)}  →  成片 ${CutPlan.tc(stats.output)}`;
    } else if (!state.asset) {
      $("totals").textContent = "先放入一条片子";
    }
    $("hint").textContent = hintText();
    $("dirty").hidden = !state.dirty;
    if (state.asset) {
      const asset = state.asset;
      const bits = [`${asset.width}×${asset.height}`, asset.fps ? `${prettyFps(asset.fps)} fps` : "", CutPlan.tc(asset.duration), asset.hasAudio ? "有声" : "无声"];
      $("meta").textContent = `${asset.name} · ${bits.filter(Boolean).join(" · ")}`;
    }
    renderList();
    const current = $("list").querySelector(".now");
    if (current && player && !player.paused) {
      const box = current.getBoundingClientRect();
      const parent = $("list").getBoundingClientRect();
      if (box.top < parent.top || box.bottom > parent.bottom) current.scrollIntoView({ block: "nearest" });
    }
  }

  function outputTime(time) {
    let acc = 0;
    for (const range of CutPlan.keptRanges(state.segments)) {
      if (time >= range.end) acc += range.end - range.start;
      else if (time > range.start) acc += time - range.start;
    }
    return acc;
  }

  function updateClock() {
    const time = player.currentTime || 0;
    $("clock").textContent = CutPlan.tc(time);
    if (!state.analysis) {
      $("clock-sub").textContent = "成片 00:00.00 / 00:00.00";
      return;
    }
    const stats = CutPlan.statsFrom(state.segments, state.analysis.duration);
    $("clock-sub").textContent = `成片 ${CutPlan.tc(outputTime(time))} / ${CutPlan.tc(stats.output)}`;
    const index = state.segments.findIndex((segment) => time >= segment.start && time < segment.end);
    if (index !== nowIndex) {
      nowIndex = index;
      renderList();
    }
  }

  function draw() {
    const dpr = window.devicePixelRatio || 1;
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    if (!width || !height) return;
    if (canvas.width !== Math.floor(width * dpr) || canvas.height !== Math.floor(height * dpr)) {
      canvas.width = Math.floor(width * dpr);
      canvas.height = Math.floor(height * dpr);
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "#100f0c";
    ctx.fillRect(0, 0, width, height);
    const duration = state.analysis ? state.analysis.duration : 0;
    if (!duration) return;
    const envelope = state.analysis.envelope || [];
    const hop = state.analysis.hop || 0.05;
    const waveTop = 28;
    const waveH = height - 70;
    const mid = waveTop + waveH / 2;
    if (envelope.length) {
      for (let x = 0; x < width; x++) {
        const t0 = (x / width) * duration;
        const t1 = ((x + 1) / width) * duration;
        const i0 = Math.max(0, Math.floor(t0 / hop));
        const i1 = Math.min(envelope.length, Math.max(i0 + 1, Math.ceil(t1 / hop)));
        let peak = -90;
        for (let i = i0; i < i1; i++) peak = Math.max(peak, envelope[i]);
        const amp = Math.max(0, Math.min(1, (peak + 58) / 58));
        const bar = Math.max(1, amp * waveH * 0.46);
        const kept = state.segments.some((segment) => segment.keep && t0 < segment.end && t1 > segment.start);
        ctx.fillStyle = kept ? "rgba(214,242,92,0.92)" : "rgba(244,240,230,0.22)";
        ctx.fillRect(x, mid - bar, 1, bar * 2);
      }
    }
    const barTop = height - 30;
    state.segments.forEach((segment, index) => {
      const x = (segment.start / duration) * width;
      const w = Math.max(1, ((segment.end - segment.start) / duration) * width);
      ctx.fillStyle = segment.keep ? "#d6f25c" : "#2a2722";
      ctx.fillRect(x, barTop, w, 18);
      if (index === state.selected) {
        ctx.strokeStyle = "#f4f0e6";
        ctx.lineWidth = 1;
        ctx.strokeRect(x + 0.5, barTop + 0.5, Math.max(0, w - 1), 17);
      }
    });
    ctx.fillStyle = "rgba(230,161,90,0.95)";
    for (const scene of state.analysis.scenes || []) {
      if (scene.score < state.settings.sceneThreshold) continue;
      ctx.fillRect((scene.t / duration) * width, 16, 1, 8);
    }
    ctx.fillStyle = "#6f6a60";
    ctx.font = "11px ui-monospace, monospace";
    ctx.textBaseline = "top";
    const px = width / duration;
    const steps = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600];
    const step = steps.find((item) => item * px >= 72) || 600;
    for (let t = 0; t <= duration + 0.001; t += step) {
      const x = (t / duration) * width;
      ctx.fillRect(x, 14, 1, 5);
      if (x < width - 48) ctx.fillText(CutPlan.tc(t), x + 4, 0);
    }
    const time = player.currentTime || 0;
    const x = Math.min(width - 1, (time / duration) * width);
    ctx.fillStyle = "#ff4d2e";
    ctx.fillRect(x, 0, 1.5, height);
    ctx.beginPath();
    ctx.moveTo(x - 5, 0);
    ctx.lineTo(x + 6, 0);
    ctx.lineTo(x + 0.5, 7);
    ctx.fill();
  }

  function timeAt(event) {
    const rect = canvas.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
    return ratio * state.analysis.duration;
  }

  function handleAt(event) {
    if (!state.analysis || !state.segments.length) return null;
    const rect = canvas.getBoundingClientRect();
    if (event.clientY - rect.top < rect.height - 40) return null;
    const time = timeAt(event);
    const px = rect.width / state.analysis.duration;
    const segment = state.segments[state.selected];
    if (!segment) return null;
    if (state.selected > 0 && Math.abs(time - segment.start) * px < 8) return "start";
    if (state.selected < state.segments.length - 1 && Math.abs(time - segment.end) * px < 8) return "end";
    return null;
  }

  function keptNow(time) {
    return CutPlan.keptRanges(state.segments).some((range) => time >= range.start - 0.02 && time < range.end - 0.04);
  }

  function ensurePlayableTime() {
    if (state.preview !== "cut" || !state.segments.length) return;
    const time = player.currentTime || 0;
    if (keptNow(time)) return;
    const ranges = CutPlan.keptRanges(state.segments);
    if (!ranges.length) return;
    const next = ranges.find((range) => range.end > time + 0.05);
    player.currentTime = (next || ranges[0]).start + 0.01;
  }

  function skipCuts() {
    if (seeking || state.preview !== "cut" || player.paused) return;
    const time = player.currentTime;
    if (keptNow(time)) return;
    const ranges = CutPlan.keptRanges(state.segments);
    if (!ranges.length) return;
    const next = ranges.find((range) => range.start > time - 0.01);
    seeking = true;
    if (next) player.currentTime = Math.min(next.end - 0.05, next.start + 0.01);
    else {
      player.pause();
      const last = ranges[ranges.length - 1];
      player.currentTime = Math.max(last.start, last.end - 0.05);
      seeking = false;
    }
  }

  function togglePlay() {
    if (!state.asset) return;
    if (player.paused) {
      ensurePlayableTime();
      player.play().catch(() => toast("这条封装浏览器播不了，分析照常进行，导出会转成 mp4"));
    } else player.pause();
  }

  function toggleSelected() {
    const segment = state.segments[state.selected];
    if (!segment) return;
    const next = state.segments.map((item) => ({ ...item }));
    next[state.selected].keep = !segment.keep;
    if (!next.some((item) => item.keep)) {
      toast("至少留一段");
      return;
    }
    state.segments = next;
    state.dirty = true;
    render();
  }

  function splitPlayhead() {
    if (!state.analysis) return;
    const next = CutPlan.splitAt(state.segments, player.currentTime || 0, state.analysis.duration);
    if (!next) {
      toast("离切口太近");
      return;
    }
    state.segments = next;
    state.dirty = true;
    render();
  }

  function nudge(delta) {
    if (!state.asset) return;
    const duration = state.analysis ? state.analysis.duration : player.duration || 0;
    player.currentTime = Math.max(0, Math.min(duration, (player.currentTime || 0) + delta));
  }

  function onSettingsChanged() {
    if (!state.settings) return;
    readSettings();
    if (!state.dirty && state.analysis) applyAutoPlan();
    render();
  }

  async function ingest(loader) {
    const mine = ++state.token;
    state.exporting = false;
    $("download").hidden = true;
    setVeil(0.04, "正在准备");
    try {
      const asset = await loader();
      if (mine !== state.token) return;
      state.asset = asset;
      state.analysis = null;
      state.segments = [];
      state.dirty = false;
      player.src = `/api/media/${asset.id}`;
      render();
      setVeil(0.12, asset.hasAudio ? "正在听气口" : "正在找镜头");
      const started = await fetch("/api/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: asset.id }),
      });
      if (!started.ok) throw new Error(await readError(started));
      const { jobId } = await started.json();
      const job = await poll(jobId, mine, (item) => setVeil(item.progress || 0.2, item.stage || "正在分析"));
      if (mine !== state.token) return;
      state.analysis = job.result;
      state.settings = settingsFor(state.analysis);
      writeSettings();
      applyAutoPlan();
      setVeil(0, "");
      render();
      const stats = CutPlan.statsFrom(state.segments, state.analysis.duration);
      toast(`剪完大约 ${CutPlan.tc(stats.output)}，原片 ${CutPlan.tc(stats.source)}`);
    } catch (err) {
      if (mine !== state.token) return;
      setVeil(0, "");
      toast(err.message || "处理失败");
      render();
    }
  }

  function chooseFile() {
    $("file").click();
  }

  async function exportCut() {
    if (!state.analysis || state.exporting) return;
    const stats = CutPlan.statsFrom(state.segments, state.analysis.duration);
    if (stats.output < 0.2) {
      toast("留下的部分太短");
      return;
    }
    state.exporting = true;
    $("download").hidden = true;
    setExportProgress(0.02, "正在导出");
    render();
    const mine = state.token;
    try {
      const response = await fetch("/api/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          id: state.asset.id,
          ranges: stats.ranges,
          aspect: state.aspect,
          normalize: state.normalize,
        }),
      });
      if (!response.ok) throw new Error(await readError(response));
      const { jobId } = await response.json();
      const job = await poll(jobId, mine, (item) => setExportProgress(item.progress || 0, item.stage || "正在导出"));
      const link = $("download");
      link.href = `/api/download/${jobId}`;
      link.download = "剪间成片.mp4";
      const dur = job.result && job.result.duration;
      link.textContent = dur ? `下载成片 ${CutPlan.tc(dur)}` : "下载成片";
      link.hidden = false;
      link.click();
      toast(dur ? `成片 ${CutPlan.tc(dur)}，已开始下载` : "成片已开始下载");
    } catch (err) {
      if (mine === state.token) toast(err.message || "导出失败");
    } finally {
      if (mine === state.token) {
        state.exporting = false;
        $("export-progress").hidden = true;
        render();
      }
    }
  }

  $("pick").addEventListener("click", chooseFile);
  $("pick-center").addEventListener("click", chooseFile);
  $("file").addEventListener("change", () => {
    const file = $("file").files && $("file").files[0];
    $("file").value = "";
    if (file) ingest(() => uploadFile(file));
  });
  $("sample").addEventListener("click", () => ingest(loadSample));
  $("sample-center").addEventListener("click", () => ingest(loadSample));

  async function loadSample() {
    setVeil(0.08, "正在做一条示例口播");
    const response = await fetch("/api/sample", { method: "POST" });
    if (!response.ok) throw new Error(await readError(response));
    const { jobId } = await response.json();
    const job = await poll(jobId, state.token, (item) => setVeil(item.progress || 0.1, item.stage || "正在做示例"));
    return job.result.asset;
  }

  document.querySelectorAll(".modes button").forEach((button) => {
    button.addEventListener("click", () => {
      if (!state.settings) return;
      state.settings.mode = button.dataset.mode;
      state.dirty = false;
      writeSettings();
      applyAutoPlan();
      render();
    });
  });
  ["threshold", "min-silence", "pad", "min-keep", "target", "clip", "scene"].forEach((id) => {
    $(id).addEventListener("input", onSettingsChanged);
  });
  $("drop-quiet").addEventListener("change", onSettingsChanged);
  document.querySelectorAll("[data-aspect]").forEach((button) => {
    button.addEventListener("click", () => {
      state.aspect = button.dataset.aspect;
      document.querySelectorAll("[data-aspect]").forEach((item) => item.classList.toggle("on", item === button));
    });
  });
  $("normalize").addEventListener("change", () => {
    state.normalize = $("normalize").checked;
  });
  document.querySelectorAll("[data-preview]").forEach((button) => {
    button.addEventListener("click", () => {
      state.preview = button.dataset.preview;
      if (state.preview === "cut" && !player.paused) ensurePlayableTime();
      render();
    });
  });

  $("play").addEventListener("click", togglePlay);
  $("export").addEventListener("click", exportCut);
  $("split").addEventListener("click", splitPlayhead);
  $("toggle").addEventListener("click", toggleSelected);
  $("reapply").addEventListener("click", () => {
    if (!state.analysis) return;
    readSettings();
    applyAutoPlan();
    render();
  });
  player.addEventListener("click", togglePlay);
  player.addEventListener("play", () => {
    $("play").textContent = "❚❚";
  });
  player.addEventListener("pause", () => {
    $("play").textContent = "▶";
  });
  player.addEventListener("timeupdate", skipCuts);
  player.addEventListener("seeked", () => {
    seeking = false;
  });
  player.addEventListener("error", () => {
    if (state.asset && !state.asset.playable) toast("浏览器播不了这个封装，分析照常进行，导出会转成 mp4");
  });

  canvas.addEventListener("pointerdown", (event) => {
    if (!state.analysis) return;
    canvas.setPointerCapture(event.pointerId);
    const handle = handleAt(event);
    drag.pointer = event.pointerId;
    drag.handle = handle;
    drag.moved = false;
    drag.x = event.clientX;
    drag.index = state.selected;
    if (!handle) {
      const time = timeAt(event);
      const index = state.segments.findIndex((segment) => time >= segment.start && time < segment.end - 0.0001);
      if (index >= 0) state.selected = index;
      player.currentTime = time;
      render();
    }
  });
  canvas.addEventListener("pointermove", (event) => {
    const handle = handleAt(event);
    canvas.style.cursor = handle ? "ew-resize" : "pointer";
    if (drag.pointer !== event.pointerId || !state.analysis) return;
    if (Math.abs(event.clientX - drag.x) > 3) drag.moved = true;
    const time = timeAt(event);
    if (drag.handle === "end") {
      state.segments = CutPlan.moveBoundary(state.segments, drag.index, time, state.analysis.duration);
      state.dirty = true;
      state.selected = Math.min(drag.index, state.segments.length - 1);
      render();
    } else if (drag.handle === "start") {
      state.segments = CutPlan.moveBoundary(state.segments, drag.index - 1, time, state.analysis.duration);
      state.dirty = true;
      render();
    } else if (drag.moved) {
      player.currentTime = time;
    }
  });
  canvas.addEventListener("pointerup", (event) => {
    if (drag.pointer !== event.pointerId) return;
    drag.pointer = null;
  });
  canvas.addEventListener("dblclick", () => {
    if (state.analysis) toggleSelected();
  });

  window.addEventListener("keydown", (event) => {
    if (event.target.closest("input, textarea, button, a, select")) return;
    if (!state.asset) return;
    if (event.code === "Space") {
      event.preventDefault();
      togglePlay();
    } else if (event.key === "x" || event.key === "X") {
      event.preventDefault();
      toggleSelected();
    } else if (event.key === "s" || event.key === "S") {
      event.preventDefault();
      splitPlayhead();
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      nudge(event.shiftKey ? -5 : -1);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      nudge(event.shiftKey ? 5 : 1);
    }
  });

  window.addEventListener("dragover", (event) => {
    if (![...event.dataTransfer.types].includes("Files")) return;
    event.preventDefault();
    $("dropveil").hidden = false;
  });
  window.addEventListener("dragleave", (event) => {
    if (event.relatedTarget) return;
    $("dropveil").hidden = true;
  });
  window.addEventListener("drop", (event) => {
    if (![...event.dataTransfer.types].includes("Files")) return;
    event.preventDefault();
    $("dropveil").hidden = true;
    const file = event.dataTransfer.files && event.dataTransfer.files[0];
    if (file) ingest(() => uploadFile(file));
  });

  function frame() {
    if (state.analysis) draw();
    updateClock();
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
  render();
})();
