"""Pure helpers for probing results and building a safe ffmpeg export graph."""

from __future__ import annotations

import math
import re

ASPECTS = {
    "16:9": (1280, 720),
    "9:16": (720, 1280),
    "1:1": (720, 720),
}

_GRAPH_OK = re.compile(r"[0-9A-Za-z_\[\]=:,.\-+/;]+")
_FPS_OK = re.compile(r"^[0-9]+/[0-9]+$|^[0-9]+(?:\.[0-9]+)?$")


def clean_fps(rate: str | None) -> str:
    if not rate or rate == "0/0":
        return "30"
    if re.fullmatch(r"[0-9]+/[0-9]+", rate):
        num, den = rate.split("/")
        if int(den) == 0:
            return "30"
        value = int(num) / int(den)
        if 1 <= value <= 120:
            return f"{int(num)}/{int(den)}"
        return "30"
    if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", rate):
        value = float(rate)
        if 1 <= value <= 120:
            text = f"{value:.3f}".rstrip("0").rstrip(".")
            return text or "30"
    return "30"


def norm_rotation(value: float | int | None) -> int:
    if value is None:
        return 0
    try:
        turned = int(round(float(value))) % 360
    except (TypeError, ValueError):
        return 0
    return turned if turned in (90, 180, 270) else 0


def clean_ranges(ranges: list[dict], duration: float) -> list[dict]:
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("原片时长无效")
    if len(ranges) > 400:
        raise ValueError("片段太多了，先合并一些")
    cleaned: list[list[float]] = []
    for item in ranges:
        try:
            start = float(item["start"])
            end = float(item["end"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("片段时间无效") from None
        if not math.isfinite(start) or not math.isfinite(end):
            raise ValueError("片段时间无效")
        start = max(0.0, min(duration, start))
        end = max(0.0, min(duration, end))
        if end - start < 0.08:
            continue
        if cleaned and start < cleaned[-1][1]:
            start = cleaned[-1][1]
            if end - start < 0.08:
                continue
        cleaned.append([start, end])
    if not cleaned:
        raise ValueError("没有可导出的片段")
    total = sum(end - start for start, end in cleaned)
    if total < 0.2:
        raise ValueError("成片太短")
    if total > duration + 0.25:
        raise ValueError("成片不能长过原片")
    return [{"start": round(start, 3), "end": round(end, 3)} for start, end in cleaned]


def parse_scene_log(text: str) -> list[dict]:
    scenes: list[dict] = []
    pending_t: float | None = None
    pending_s: float | None = None
    for line in text.splitlines():
        time_match = re.search(r"pts_time:([0-9]+(?:\.[0-9]+)?)", line)
        score_match = re.search(r"scene_score=([0-9]+(?:\.[0-9]+)?)", line)
        if time_match:
            pending_t = float(time_match.group(1))
        if score_match:
            pending_s = float(score_match.group(1))
        if pending_t is not None and pending_s is not None:
            scenes.append({"t": round(pending_t, 3), "score": round(pending_s, 4)})
            pending_t = None
            pending_s = None
    return scenes


def _rotation_filters(rotation: int) -> str:
    if rotation == 90:
        return "transpose=1,"
    if rotation == 270:
        return "transpose=2,"
    if rotation == 180:
        return "hflip,vflip,"
    return ""


def build_export_filter(
    ranges: list[dict],
    *,
    fps: str,
    has_audio: bool,
    aspect: str,
    normalize: bool,
    rotation: int,
) -> tuple[str, str, str | None]:
    fps = clean_fps(fps)
    if not _FPS_OK.fullmatch(fps):
        raise ValueError("帧率无效")
    if aspect not in ("original", *ASPECTS):
        raise ValueError("画面比例无效")
    rotation = norm_rotation(rotation)
    parts: list[str] = []
    rotate = _rotation_filters(rotation)
    video_in = "0:v"
    if rotate:
        parts.append(f"[0:v]{rotate[:-1]}[base]")
        video_in = "base"

    for index, item in enumerate(ranges):
        start = float(item["start"])
        end = float(item["end"])
        if not math.isfinite(start) or not math.isfinite(end) or end <= start:
            raise ValueError("片段时间无效")
        dur = end - start
        parts.append(
            f"[{video_in}]trim=start={start:.3f}:end={end:.3f},"
            f"setpts=PTS-STARTPTS,fps={fps},format=yuv420p,settb=AVTB[v{index}]"
        )
        if has_audio:
            fade = ""
            if dur > 0.08:
                fade_d = min(0.02, dur / 4)
                fade = (
                    f",afade=t=in:st=0:d={fade_d:.3f},"
                    f"afade=t=out:st={max(0.0, dur - fade_d):.3f}:d={fade_d:.3f}"
                )
            # apad then trim forces each audio piece to the same length as its picture,
            # which is what concat requires.
            parts.append(
                f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS,"
                f"aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                f"apad=pad_dur=1,atrim=start=0:end={dur:.3f}{fade}[a{index}]"
            )

    count = len(ranges)
    if has_audio:
        joined = "".join(f"[v{i}][a{i}]" for i in range(count))
        parts.append(f"{joined}concat=n={count}:v=1:a=1[vc][ac]")
    else:
        joined = "".join(f"[v{i}]" for i in range(count))
        parts.append(f"{joined}concat=n={count}:v=1:a=0[vc]")

    video_out = "[vc]"
    if aspect in ASPECTS:
        width, height = ASPECTS[aspect]
        parts.append(
            f"{video_out}scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1[vout]"
        )
        video_out = "[vout]"
    else:
        parts.append(f"{video_out}setsar=1[vout]")
        video_out = "[vout]"

    audio_out = None
    if has_audio:
        audio_out = "[ac]"
        if normalize:
            parts.append(f"{audio_out}loudnorm=I=-16:TP=-1.5:LRA=11[aout]")
            audio_out = "[aout]"

    graph = ";".join(parts)
    if not _GRAPH_OK.fullmatch(graph):
        raise RuntimeError("内部滤镜拼装失败")
    return graph, video_out, audio_out
