"""End-to-end check against a running 剪间 server."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"


def request(method: str, path: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode())


def poll(job_id: str) -> dict:
    deadline = time.time() + 90
    while time.time() < deadline:
        job = request("GET", f"/api/jobs/{job_id}")
        if job["status"] == "done":
            return job
        if job["status"] == "error":
            raise RuntimeError(job["error"])
        time.sleep(0.3)
    raise TimeoutError(job_id)


def plan(analysis: dict, mode: str, **extra) -> dict:
    settings = {
        "mode": mode,
        "thresholdDb": -36,
        "minSilence": 0.3,
        "pad": 0.06,
        "minKeep": 0.28,
        "target": 6,
        "clip": 2.2,
        "sceneThreshold": 0.28,
        "dropQuiet": False,
    }
    settings.update(extra)
    script = """
const { buildPlan } = require("./static/plan.js");
let raw = "";
process.stdin.on("data", (chunk) => raw += chunk);
process.stdin.on("end", () => {
  const body = JSON.parse(raw);
  process.stdout.write(JSON.stringify(buildPlan(body.analysis, body.settings)));
});
"""
    proc = subprocess.run(
        ["node", "-e", script],
        input=json.dumps({"analysis": analysis, "settings": settings}).encode(),
        stdout=subprocess.PIPE,
        check=True,
    )
    return json.loads(proc.stdout.decode())


def probe(path: str) -> dict:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)


def main() -> None:
    sample = poll(request("POST", "/api/sample")["jobId"])
    asset = sample["result"]["asset"]
    assert asset["hasAudio"], asset
    assert asset["duration"] > 15, asset
    analysis = poll(request("POST", "/api/analyze", {"id": asset["id"]})["jobId"])["result"]
    assert len(analysis["envelope"]) > 200, len(analysis["envelope"])
    strong = [scene for scene in analysis["scenes"] if scene["score"] >= 0.3]
    assert len(strong) >= 2, analysis["scenes"][:8]

    silence = plan(analysis, "silence")
    assert 8 <= silence["output"] <= 13, silence
    assert silence["saved"] > 3, silence

    highlight = plan(analysis, "highlight", target=6, clip=2.2)
    assert 4.5 <= highlight["output"] <= 6.4, highlight

    exported = poll(
        request(
            "POST",
            "/api/export",
            {"id": asset["id"], "ranges": silence["ranges"], "aspect": "9:16", "normalize": True},
        )["jobId"]
    )
    assert abs(exported["result"]["duration"] - silence["output"]) < 0.6, exported["result"]
    assert exported["result"]["width"] == 720, exported["result"]
    assert exported["result"]["height"] == 1280, exported["result"]

    print("ok", {"silence": silence["output"], "highlight": highlight["output"], "scenes": len(strong)})


if __name__ == "__main__":
    try:
        main()
    except urllib.error.URLError as exc:
        print("server not reachable", exc, file=sys.stderr)
        sys.exit(1)
