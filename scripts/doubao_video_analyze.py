#!/usr/bin/env python3
"""Local-video helper for Ark/Doubao chat-completions video understanding.

This legacy path sends a local video as a base64 data URL. Prefer
ark_files_responses_video_analyze.py for larger files because Files API avoids
large JSON request bodies. Secrets are read from environment variables and are
never put into subprocess arguments.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import requests

DEFAULT_MODEL = os.environ.get("DOUBAO_VIDEO_MODEL", "doubao-seed-2-0-pro-260215")
ARK_URL = os.environ.get("DOUBAO_BASE_URL", "https://ark.cn-beijing.volces.com/api/coding/v3").rstrip("/") + "/chat/completions"

PROMPT = """直接输出最终答案，不要展开思考过程。请用中文分析这个短视频：
1. 原视频在讲什么
2. 画面逐段
3. 口播或字幕核心
4. 前3秒钩子
5. 内容结构
6. 可复用结构或迁移建议
如果画面不清晰，请明确说明不确定点。"""


class SkillRunError(Exception):
    def __init__(self, stage: str, error: str, next_action: str, details: dict[str, Any] | None = None):
        super().__init__(error)
        self.stage = stage
        self.error = error
        self.next_action = next_action
        self.details = details or {}

    def to_summary(self) -> dict[str, Any]:
        obj: dict[str, Any] = {
            "ok": False,
            "stage": self.stage,
            "error": self.error,
            "next_action": self.next_action,
        }
        if self.details:
            obj["details"] = self.details
        return obj


def print_error(stage: str, error: str, next_action: str, details: dict[str, Any] | None = None) -> None:
    print(json.dumps(SkillRunError(stage, error, next_action, details).to_summary(), ensure_ascii=False, indent=2))


def prepare_private_out_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def run_cmd(cmd: list[str], timeout: int | None = None) -> str:
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd)}\n{proc.stderr[-1000:]}")
    return proc.stdout


def ffprobe_duration(path: Path) -> float:
    try:
        out = run_cmd([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ], timeout=30).strip()
        return float(out)
    except Exception:
        return 0.0


def make_proxy(src: Path, out_dir: Path, max_mb: float, width: int, crf: int) -> Path:
    size_mb = src.stat().st_size / 1024 / 1024
    if size_mb <= max_mb:
        return src
    prepare_private_out_dir(out_dir)
    dst = out_dir / f"{src.stem}_proxy_{width}_crf{crf}.mp4"
    try:
        run_cmd([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
            "-vf", f"scale={width}:-2", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", str(crf), "-c:a", "aac", "-b:a", "32k", str(dst),
        ], timeout=1800)
    except FileNotFoundError as exc:
        raise SkillRunError(
            "make_video_proxy",
            f"{type(exc).__name__}: ffmpeg not found",
            "Install ffmpeg or use the Files API runner with a smaller local video. Do not claim proxy generation succeeded.",
        ) from exc
    return dst


def redact_request(req: dict[str, Any], raw_bytes: int) -> dict[str, Any]:
    redacted = json.loads(json.dumps(req, ensure_ascii=False))
    try:
        redacted["messages"][0]["content"][0]["video_url"]["url"] = f"data:video/mp4;base64,<redacted {raw_bytes} bytes>"
    except Exception:
        pass
    return redacted


def post_json(req: dict[str, Any], api_key: str, timeout: int) -> tuple[dict[str, Any], dict[str, Any], bytes]:
    data = json.dumps(req, ensure_ascii=False).encode("utf-8")
    t0 = time.time()
    resp = requests.post(
        ARK_URL,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        data=data,
        timeout=timeout,
    )
    try:
        obj = resp.json()
    except Exception:
        obj = {"raw": resp.text[:2000]}
    metrics = {
        "http": resp.status_code,
        "elapsed_sec": round(time.time() - t0, 3),
        "request_bytes": len(data),
        "response_bytes": len(resp.content),
    }
    return metrics, obj, resp.content


def extract_content(obj: dict[str, Any]) -> str:
    try:
        return obj.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
    except Exception:
        return ""


def looks_like_false_success(content: str) -> bool:
    text = content.strip().lower()
    if not text:
        return True
    markers = [
        "未提供视频",
        "无法查看视频",
        "请提供视频",
        "没有视频内容",
        "作为一个文本模型",
        "i cannot view",
        "cannot access the video",
        "please provide the video",
    ]
    return any(marker in text for marker in markers)


def main() -> None:
    try:
        run()
    except SkillRunError as exc:
        print(json.dumps(exc.to_summary(), ensure_ascii=False, indent=2))
        sys.exit(1)
    except Exception as exc:
        print_error(
            "unexpected",
            f"{type(exc).__name__}: {exc}",
            "Inspect the input file, credentials, dependencies, and output directory. Do not claim analysis succeeded until ok=true.",
        )
        sys.exit(1)


def run() -> None:
    parser = argparse.ArgumentParser(description="Analyze a local video through Ark/Doubao chat-completions as a base64 data URL.")
    parser.add_argument("video", help="local video file path; do not pass http(s), file://, or a directory")
    parser.add_argument("--out-dir", default="/tmp/doubao-video-analysis")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-mb-direct", type=float, default=2.0, help="Only send original directly when file is <= this size; otherwise create a proxy.")
    parser.add_argument("--width", type=int, default=540)
    parser.add_argument("--crf", type=int, default=34)
    parser.add_argument("--fps", type=float, default=1.0)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--prompt", default=PROMPT)
    parser.add_argument("--save-raw-artifacts", action="store_true", help="save full request.json containing the base64 video; private debugging only")
    args = parser.parse_args()

    api_key = os.environ.get("DOUBAO_API_KEY") or os.environ.get("ARK_API_KEY")
    if not api_key:
        print_error(
            "configuration",
            "missing DOUBAO_API_KEY/ARK_API_KEY",
            "Set DOUBAO_API_KEY or ARK_API_KEY in the environment before running video understanding.",
        )
        sys.exit(2)

    src = Path(args.video).expanduser().resolve()
    if not src.exists() or not src.is_file():
        raise SkillRunError(
            "input",
            f"Local video file not found: {src}",
            "Provide a real local MP4/MOV file path. Do not pass a URL to this local-file helper.",
        )

    out_dir = Path(args.out_dir)
    prepare_private_out_dir(out_dir)
    proxy = make_proxy(src, out_dir, args.max_mb_direct, args.width, args.crf)
    raw_video = proxy.read_bytes()
    b64 = base64.b64encode(raw_video).decode("ascii")
    req: dict[str, Any] = {
        "model": args.model,
        "messages": [{"role": "user", "content": [
            {"type": "video_url", "video_url": {"url": "data:video/mp4;base64," + b64, "fps": args.fps}},
            {"type": "text", "text": args.prompt},
        ]}],
        "thinking": {"type": "disabled"},
        "max_completion_tokens": 1600,
        "stream": False,
    }

    request_path = out_dir / ("request.json" if args.save_raw_artifacts else "request.redacted.json")
    if args.save_raw_artifacts:
        request_path.write_text(json.dumps(req, ensure_ascii=False), encoding="utf-8")
    else:
        request_path.write_text(json.dumps(redact_request(req, len(raw_video)), ensure_ascii=False, indent=2), encoding="utf-8")

    metrics, obj, body = post_json(req, api_key, args.timeout)
    response_path = out_dir / "response.json"
    response_path.write_bytes(body)
    content = extract_content(obj)
    summary = {
        "ok": 200 <= metrics["http"] < 300 and bool(content) and not looks_like_false_success(content),
        "mode": "base64_video_url_chat",
        "http": metrics["http"],
        "elapsed_sec": metrics["elapsed_sec"],
        "source": str(src),
        "source_bytes": src.stat().st_size,
        "sent_video": str(proxy),
        "sent_video_bytes": proxy.stat().st_size,
        "request_bytes": metrics["request_bytes"],
        "response_bytes": metrics["response_bytes"],
        "duration_sec": round(ffprobe_duration(src), 3),
        "usage": obj.get("usage") if isinstance(obj, dict) else None,
        "content": content,
        "error": obj.get("error") if isinstance(obj, dict) else None,
        "artifacts_private": True,
        "saved_full_base64_request": bool(args.save_raw_artifacts),
        "next_action": "Use this content as the video-understanding source." if content and not looks_like_false_success(content) else "Treat this as a failed or untrusted base64 attempt and fall back to Files API or ASR plus keyframes.",
        "paths": {
            "out_dir": str(out_dir),
            "request": str(request_path),
            "response": str(response_path),
        },
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
