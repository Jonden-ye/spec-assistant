"""剪间 — local automatic video cutting workbench."""

from __future__ import annotations

import array
import json
import math
import os
import random
import re
import struct
import subprocess
import threading
import time
import traceback
import uuid
import wave
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from edit import (
    build_export_filter,
    clean_fps,
    clean_ranges,
    norm_rotation,
    parse_scene_log,
)

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
DATA = ROOT / "data"
EXPORTS = DATA / "exports"
DATA.mkdir(parents=True, exist_ok=True)
EXPORTS.mkdir(parents=True, exist_ok=True)

MAX_BYTES = 400 * 1024 * 1024
MAX_DURATION = 20 * 60
HOP = 0.05
ALLOWED = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
MIME = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
}
PLAYABLE = {".mp4", ".m4v", ".mov", ".webm"}
FONT_CANDIDATES = [
    Path("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/noto/NotoSansSC-Regular.otf"),
    Path("/System/Library/Fonts/PingFang.ttc"),
    Path("/Library/Fonts/Arial Unicode.ttf"),
    Path("C:/Windows/Fonts/msyh.ttc"),
    Path("C:/Windows/Fonts/msyh.ttf"),
]
ID_RE = re.compile(r"^[a-f0-9]{32}$")

app = FastAPI(title="剪间")
assets: dict[str, dict] = {}
jobs: dict[str, dict] = {}
lock = threading.Lock()
heavy = threading.Semaphore(1)


class ExportIn(BaseModel):
    id: str
    ranges: list[dict]
    aspect: str = "original"
    normalize: bool = True

    model_config = {"extra": "ignore"}


class IdIn(BaseModel):
    id: str


def find_cjk_font() -> Path | None:
    for path in FONT_CANDIDATES:
        if path.is_file():
            return path
    return None


def public_error(text: str) -> str:
    lines = []
    for line in text.splitlines():
        cleaned = re.sub(r"/+\S+", "…", line).strip()
        if not cleaned or cleaned.startswith("frame=") or cleaned.startswith("size="):
            continue
        lines.append(cleaned)
    message = " ".join(lines[-3:]) if lines else "处理失败"
    return message[:240]


def require_id(value: str) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise HTTPException(404, "找不到这条素材")
    return value


def safe_name(name: str | None) -> str:
    base = Path(name or "未命名").name
    base = re.sub(r"[\x00-\x1f]", "", base).strip() or "未命名"
    return base[:80]


def get_asset(asset_id: str) -> dict:
    require_id(asset_id)
    with lock:
        asset = assets.get(asset_id)
    if not asset:
        raise HTTPException(404, "找不到这条素材，重新上传一次")
    return asset


def probe(path: Path) -> dict:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=40,
    )
    if proc.returncode != 0:
        raise RuntimeError(public_error(proc.stderr) or "读不出这条视频")
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams") or []
    video = next(
        (
            stream
            for stream in streams
            if stream.get("codec_type") == "video" and (stream.get("disposition") or {}).get("attached_pic") != 1
        ),
        None,
    )
    if video is None:
        raise RuntimeError("这个文件里没有画面")
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    duration = float((data.get("format") or {}).get("duration") or video.get("duration") or 0)
    if not math.isfinite(duration) or duration < 0.3:
        raise RuntimeError("这条片子太短了")
    if duration > MAX_DURATION + 1:
        raise RuntimeError("先支持 20 分钟以内的片子")
    rotation = 0
    tags = video.get("tags") or {}
    if "rotate" in tags:
        rotation = norm_rotation(tags.get("rotate"))
    for side in video.get("side_data_list") or []:
        if "rotation" in side:
            rotation = norm_rotation(side.get("rotation"))
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    if rotation in (90, 270):
        width, height = height, width
    return {
        "duration": round(duration, 3),
        "width": width,
        "height": height,
        "fps": clean_fps(str(video.get("avg_frame_rate") or video.get("r_frame_rate") or "30")),
        "hasAudio": audio is not None,
        "rotation": rotation,
    }


def public_asset(asset_id: str, asset: dict) -> dict:
    return {
        "id": asset_id,
        "name": asset["name"],
        "duration": asset["duration"],
        "width": asset["width"],
        "height": asset["height"],
        "fps": asset["fps"],
        "hasAudio": asset["hasAudio"],
        "playable": asset["playable"],
    }


def remember(path: Path, name: str, meta: dict) -> dict:
    asset_id = uuid.uuid4().hex
    ext = path.suffix.lower()
    asset = {
        **meta,
        "path": str(path),
        "name": name,
        "playable": ext in PLAYABLE,
        "mime": MIME.get(ext, "application/octet-stream"),
    }
    with lock:
        assets[asset_id] = asset
    return public_asset(asset_id, asset)


def update_job(job_id: str, **fields) -> None:
    with lock:
        if job_id in jobs:
            jobs[job_id].update(fields)


def job_view(job: dict) -> dict:
    return {
        "id": job["id"],
        "status": job["status"],
        "progress": job["progress"],
        "stage": job["stage"],
        "error": job["error"],
        "result": job["result"] if job["status"] == "done" else None,
    }


def submit(kind: str, fn) -> str:
    job_id = uuid.uuid4().hex
    with lock:
        jobs[job_id] = {
            "id": job_id,
            "kind": kind,
            "status": "running",
            "progress": 0,
            "stage": "排队",
            "error": None,
            "result": None,
        }

    def runner() -> None:
        try:
            with heavy:
                result = fn(job_id)
            update_job(job_id, status="done", progress=1, stage="完成", result=result)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else "处理失败"
            update_job(job_id, status="error", error=detail)
        except Exception as exc:
            traceback.print_exc()
            update_job(job_id, status="error", error=public_error(str(exc)) or "处理失败")

    threading.Thread(target=runner, daemon=True).start()
    return job_id


def run_ffmpeg(args: list[str], total: float, on_progress, timeout: float) -> str:
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    logs: list[str] = []
    started = time.time()
    assert proc.stderr is not None
    try:
        for line in proc.stderr:
            logs.append(line)
            if time.time() - started > timeout:
                proc.kill()
                proc.wait(timeout=5)
                raise RuntimeError("处理超时")
            stripped = line.strip()
            if stripped.startswith("out_time=") and not stripped.startswith("out_time_ms") and total > 0:
                parts = stripped.split("=", 1)[1].split(":")
                if len(parts) == 3:
                    try:
                        seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
                    except ValueError:
                        continue
                    on_progress(min(0.99, max(0.0, seconds / total)))
        code = proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise RuntimeError("处理超时") from None
    if code != 0:
        raise RuntimeError(public_error("".join(logs)) or "ffmpeg 失败")
    return "".join(logs)


def read_envelope(path: Path, duration: float) -> list[float]:
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-vn", "-i", str(path), "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=max(60, duration * 2),
    )
    if proc.returncode != 0:
        raise RuntimeError(public_error(proc.stderr.decode("utf-8", "replace")) or "读音轨失败")
    data = proc.stdout
    usable = data[: len(data) - (len(data) % 2)]
    samples = array.array("h")
    samples.frombytes(usable)
    hop_n = int(8000 * HOP)
    envelope: list[float] = []
    for index in range(0, len(samples), hop_n):
        chunk = samples[index : index + hop_n]
        if len(chunk) < hop_n // 4:
            break
        acc = 0
        for sample in chunk:
            acc += sample * sample
        rms = math.sqrt(acc / len(chunk))
        db = -90.0 if rms < 1 else max(-90.0, min(0.0, 20 * math.log10(rms / 32768.0)))
        envelope.append(round(db, 1))
    return envelope


def read_scenes(path: Path, duration: float, on_progress) -> list[dict]:
    log = run_ffmpeg(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(path),
            "-vf",
            "fps=8,select='gte(scene\\,0.04)',metadata=print",
            "-an",
            "-f",
            "null",
            "-",
            "-progress",
            "pipe:2",
            "-nostats",
        ],
        duration,
        on_progress,
        timeout=max(90, duration * 3),
    )
    return parse_scene_log(log)


def analyze(job_id: str, asset_id: str) -> dict:
    asset = get_asset(asset_id)
    path = Path(asset["path"])
    update_job(job_id, progress=0.08, stage="读取画面")
    envelope: list[float] = []
    if asset["hasAudio"]:
        update_job(job_id, progress=0.18, stage="量音量")
        envelope = read_envelope(path, asset["duration"])
    update_job(job_id, progress=0.42, stage="找镜头切换")
    scenes = read_scenes(
        path,
        asset["duration"],
        lambda fraction: update_job(job_id, progress=0.42 + 0.5 * fraction, stage="找镜头切换"),
    )
    return {
        "id": asset_id,
        "duration": asset["duration"],
        "hasAudio": asset["hasAudio"],
        "hop": HOP,
        "envelope": envelope,
        "scenes": scenes,
    }


def export_cut(job_id: str, body: ExportIn) -> dict:
    if body.aspect not in ("original", "16:9", "9:16", "1:1"):
        raise RuntimeError("画面比例无效")
    asset = get_asset(body.id)
    ranges = clean_ranges(body.ranges, asset["duration"])
    total = sum(item["end"] - item["start"] for item in ranges)
    graph, video_map, audio_map = build_export_filter(
        ranges,
        fps=asset["fps"],
        has_audio=asset["hasAudio"],
        aspect=body.aspect,
        normalize=body.normalize and asset["hasAudio"],
        rotation=asset["rotation"],
    )
    dest = EXPORTS / f"{job_id}.mp4"
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-i",
        asset["path"],
        "-filter_complex",
        graph,
        "-map",
        video_map,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
    ]
    if audio_map:
        cmd += ["-map", audio_map, "-c:a", "aac", "-b:a", "160k"]
    else:
        cmd += ["-an"]
    cmd += ["-progress", "pipe:2", "-nostats", str(dest)]
    update_job(job_id, progress=0.02, stage="正在导出")
    run_ffmpeg(
        cmd,
        total,
        lambda fraction: update_job(job_id, progress=0.02 + 0.94 * fraction, stage="正在导出"),
        timeout=max(120, total * 8),
    )
    probed = probe(dest)
    return {"duration": probed["duration"], "width": probed["width"], "height": probed["height"]}


def escape_filter_path(path: Path) -> str:
    text = path.as_posix()
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def write_sample_wav(path: Path, duration: float = 16.0, sr: int = 44100) -> None:
    phrases = [
        (0.7, 3.0, 0.34, 196),
        (4.6, 7.2, 0.30, 220),
        (8.8, 11.3, 0.62, 262),
        (12.8, 15.1, 0.32, 174),
    ]
    rng = random.Random(7)
    count = int(sr * duration)
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sr)
        frames = bytearray()
        for index in range(count):
            t = index / sr
            value = 0.0
            for start, end, amp, freq in phrases:
                if start <= t < end:
                    local = t - start
                    phase = (local % 0.2) / 0.2
                    syllable = phase / 0.12 if phase < 0.12 else (1 - phase) / 0.88
                    syllable = max(0.0, syllable) ** 0.7
                    if (local % 0.74) > 0.6:
                        syllable *= 0.2
                    wobble = 1 + 0.03 * math.sin(2 * math.pi * 4 * t)
                    value = syllable * amp * (
                        0.7 * math.sin(2 * math.pi * freq * wobble * t)
                        + 0.2 * math.sin(2 * math.pi * freq * 2.02 * t)
                        + 0.1 * math.sin(2 * math.pi * (freq * 0.5) * t)
                    )
                    break
            value += (rng.random() * 2 - 1) * 0.0035
            value = max(-1.0, min(1.0, value))
            frames += struct.pack("<h", int(value * 32767))
            if len(frames) >= sr * 4:
                handle.writeframes(frames)
                frames.clear()
        if frames:
            handle.writeframes(frames)


def sample_filter(font: Path | None) -> str:
    captions = [
        (0.7, 3.0, "第一句 先把气口剪掉"),
        (4.6, 7.2, "第二句 停顿不用留"),
        (8.8, 11.3, "第三句 这一段最有劲"),
        (12.8, 15.1, "最后一句 按镜头拆开"),
    ]
    draws = ["drawbox=x=72:y=150:w=18:h=18:color=0xD6F25C:t=fill"]
    if font is not None:
        font_arg = escape_filter_path(font)
        draws.append(
            f"drawtext=fontfile={font_arg}:text='示例口播':fontsize=26:fontcolor=0xD6F25C:x=108:y=146"
        )
        for start, end, text in captions:
            draws.append(
                "drawtext="
                f"fontfile={font_arg}:text='{text}':fontsize=44:fontcolor=0xF4F0E6:"
                "x=(w-text_w)/2:y=(h-text_h)/2:"
                f"enable='between(t\\,{start}\\,{end})'"
            )
    draws.append("drawbox=x=0:y=ih-10:w=iw:h=10:color=0xD6F25C:t=fill")
    chain = ",".join(draws)
    return f"[0:v][1:v][2:v]concat=n=3:v=1:a=0[base];[base]{chain}[v]"


def make_sample(job_id: str) -> dict:
    update_job(job_id, progress=0.1, stage="正在做一条示例口播")
    token = uuid.uuid4().hex
    wav = DATA / f"{token}.wav"
    script = DATA / f"{token}.ffscript"
    dest = DATA / f"{token}.mp4"
    try:
        write_sample_wav(wav)
        script.write_text(sample_filter(find_cjk_font()), encoding="utf-8")
        # Cuts sit in the silent gaps, and the colors are far enough apart
        # that scene detection marks them at the default sensitivity.
        clips = [(3.8, "0x1a1814"), (8.2, "0x1d4e89"), (4.0, "0xc46a32")]
        cmd = ["ffmpeg", "-y", "-hide_banner"]
        for dur, color in clips:
            cmd += ["-f", "lavfi", "-t", str(dur), "-i", f"color=c={color}:s=1280x720:r=25"]
        cmd += [
            "-i",
            str(wav),
            "-filter_complex_script",
            str(script),
            "-map",
            "[v]",
            "-map",
            "3:a",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "22",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-t",
            "16",
            str(dest),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        if proc.returncode != 0 or not dest.exists():
            raise RuntimeError(public_error(proc.stderr) or "示例生成失败")
        meta = probe(dest)
        update_job(job_id, progress=0.9, stage="示例好了")
        return {"asset": remember(dest, "示例口播.mp4", meta)}
    finally:
        wav.unlink(missing_ok=True)
        script.unlink(missing_ok=True)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


@app.get("/api/font")
def font() -> Response:
    path = find_cjk_font()
    if path is None:
        raise HTTPException(404, "没有中文字体")
    media = "font/ttf"
    if path.suffix.lower() == ".otf":
        media = "font/otf"
    elif path.suffix.lower() == ".ttc":
        media = "font/collection"
    return FileResponse(path, media_type=media, content_disposition_type="inline")


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)) -> dict:
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED:
        raise HTTPException(400, "只支持 mp4、mov、webm、mkv、avi")
    dest = DATA / f"{uuid.uuid4().hex}{ext}"
    size = 0
    try:
        with dest.open("wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(400, "文件超过 400MB")
                handle.write(chunk)
        if size < 1024:
            raise HTTPException(400, "这个文件是空的")
        meta = probe(dest)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    except Exception as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, public_error(str(exc)) or "读不出这条视频") from None
    return remember(dest, safe_name(file.filename), meta)


@app.post("/api/sample")
def sample() -> dict:
    return {"jobId": submit("sample", make_sample)}


@app.post("/api/analyze")
def analyze_route(body: IdIn) -> dict:
    asset_id = require_id(body.id)
    get_asset(asset_id)
    return {"jobId": submit("analyze", lambda job_id: analyze(job_id, asset_id))}


@app.post("/api/export")
def export_route(body: ExportIn) -> dict:
    require_id(body.id)
    get_asset(body.id)
    if not isinstance(body.ranges, list) or not body.ranges:
        raise HTTPException(400, "没有可导出的片段")
    return {"jobId": submit("export", lambda job_id: export_cut(job_id, body))}


@app.get("/api/jobs/{job_id}")
def job(job_id: str) -> dict:
    require_id(job_id)
    with lock:
        found = jobs.get(job_id)
    if not found:
        raise HTTPException(404, "找不到这个任务")
    return job_view(found)


@app.get("/api/media/{asset_id}")
def media(asset_id: str) -> FileResponse:
    asset = get_asset(asset_id)
    return FileResponse(asset["path"], media_type=asset["mime"], content_disposition_type="inline")


@app.get("/api/download/{job_id}")
def download(job_id: str) -> FileResponse:
    require_id(job_id)
    with lock:
        found = jobs.get(job_id)
    if not found or found["status"] != "done" or found["kind"] != "export":
        raise HTTPException(404, "成片还没好")
    path = EXPORTS / f"{job_id}.mp4"
    if not path.is_file():
        raise HTTPException(404, "成片还没好")
    return FileResponse(path, media_type="video/mp4", filename="剪间成片.mp4")


app.mount("/static", StaticFiles(directory=STATIC), name="static")


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8765"))
    print(f"剪间已启动 http://127.0.0.1:{port}", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=port)
