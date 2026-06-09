#!/usr/bin/env python3
"""Analyze a Douyin share/video URL with Doubao video understanding.

Stable path discovered in production:
1. Resolve v.douyin.com to iesdouyin share page.
2. Parse window._ROUTER_DATA for metadata and aweme play URL.
3. Locally follow the aweme.snssdk.com play URL redirect to the final douyinvod CDN URL.
4. Send the final CDN URL to Doubao/Volcano Ark chat-completions.

This avoids large base64 uploads, which can stall before the request body is fully uploaded.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import requests

DEFAULT_MODEL = os.environ.get("DOUBAO_VIDEO_MODEL", "doubao-seed-2-0-pro-260215")
DEFAULT_BASE = os.environ.get("DOUBAO_BASE_URL", "https://ark.cn-beijing.volces.com/api/coding/v3").rstrip("/")
ARK_URL = DEFAULT_BASE + "/chat/completions"
MOBILE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148"

PROMPT = """直接输出最终答案，不要展开思考过程。请用中文分析这条抖音视频：
1. 原视频在讲什么
2. 画面/口播/字幕核心
3. 前3秒钩子
4. 内容结构
5. 最值钱的部分
6. 可复用 SOP 或迁移建议
如果有不确定内容，请明确标注。"""


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


def _print_error(stage: str, error: str, next_action: str, details: dict[str, Any] | None = None) -> None:
    print(json.dumps(SkillRunError(stage, error, next_action, details).to_summary(), ensure_ascii=False, indent=2))


def _prepare_private_out_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def _extract_aweme_id(text: str, final_url: str) -> str | None:
    candidates = re.findall(r"/(?:video|note)/(\d+)", final_url + "\n" + text[:5000])
    return candidates[0] if candidates else None


def _load_router_data(html: str) -> dict[str, Any] | None:
    m = re.search(r"<script>window\._ROUTER_DATA\s*=\s*(\{.*?\})</script>", html, re.S)
    if not m:
        return None
    return json.loads(m.group(1))


def _parse_int(v: str | None) -> int | None:
    if not v:
        return None
    try:
        return int(v)
    except Exception:
        return None


def _iter_video_url_candidates(video: dict[str, Any]) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    for key in ("play_addr", "download_addr", "play_addr_h264", "play_addr_bytevc1"):
        for u in video.get(key, {}).get("url_list", []) or []:
            if "playwm" in u:
                candidates.append((f"{key}.no_watermark", u.replace("playwm", "play")))
            candidates.append((key, u))
    for i, br in enumerate(video.get("bit_rate", []) or []):
        for u in br.get("play_addr", {}).get("url_list", []) or []:
            if "playwm" in u:
                candidates.append((f"bit_rate[{i}].play_addr.no_watermark", u.replace("playwm", "play")))
            candidates.append((f"bit_rate[{i}].play_addr", u))

    seen: set[str] = set()
    uniq: list[tuple[str, str]] = []
    for source, u in candidates:
        if not u or u in seen:
            continue
        seen.add(u)
        uniq.append((source, u))
    return uniq


def _resolve_first_working_video_url(
    session: requests.Session,
    candidates: list[tuple[str, str]],
    referer: str,
    timeout: int,
    allow_unverified: bool,
) -> tuple[str, str, str, int | None, list[dict[str, Any]]]:
    attempts: list[dict[str, Any]] = []
    for source, candidate_url in candidates:
        attempt: dict[str, Any] = {"source": source, "url": candidate_url}
        try:
            resp = session.get(
                candidate_url,
                headers={"User-Agent": "Mozilla/5.0", "Referer": referer},
                allow_redirects=True,
                timeout=timeout,
                stream=True,
            )
            attempt.update({
                "http": resp.status_code,
                "final_url": resp.url,
                "content_type": resp.headers.get("Content-Type"),
                "content_length": resp.headers.get("Content-Length"),
            })
            if 200 <= resp.status_code < 400:
                final_url = resp.url
                content_length = _parse_int(resp.headers.get("Content-Length"))
                resp.close()
                attempts.append(attempt)
                return source, candidate_url, final_url, content_length, attempts
            resp.close()
        except Exception as e:
            attempt["error"] = f"{type(e).__name__}: {e}"
        attempts.append(attempt)

    brief = [
        {
            "source": a.get("source"),
            "http": a.get("http"),
            "error": a.get("error"),
            "final_url": a.get("final_url"),
        }
        for a in attempts
    ]
    if allow_unverified:
        fallback = next(((source, u) for source, u in candidates if "no_watermark" in source), None)
        if fallback is None and candidates:
            fallback = candidates[0]
        if fallback is not None:
            source, candidate_url = fallback
            return f"{source}.unverified", candidate_url, candidate_url, None, attempts

    raise SkillRunError(
        "resolve_douyin_source",
        "No verified Douyin video URL candidate was reachable.",
        "Ask for an accessible share link or the original video file. Do not send unverified candidates unless --allow-unverified-url is explicitly chosen.",
        {"attempts": brief},
    )


def resolve_douyin(url: str, timeout: int, allow_unverified: bool = False) -> dict[str, Any]:
    session = requests.Session()
    headers = {"User-Agent": MOBILE_UA}
    r = session.get(url, headers=headers, allow_redirects=True, timeout=timeout)
    r.raise_for_status()
    html = r.text
    aweme_id = _extract_aweme_id(html, r.url)
    if not aweme_id:
        raise SkillRunError(
            "resolve_douyin_source",
            f"Could not extract an aweme id from final URL: {r.url}",
            "Open the link in a browser or provide the original video file. Do not infer video content without a media source.",
        )

    # Prefer iesdouyin share page because it exposes SSR router data without browser automation.
    share_page = f"https://www.iesdouyin.com/share/video/{aweme_id}/"
    r2 = session.get(share_page, headers=headers, timeout=timeout)
    r2.raise_for_status()
    router = _load_router_data(r2.text)
    if not router:
        raise SkillRunError(
            "resolve_douyin_source",
            "Could not parse window._ROUTER_DATA from the iesdouyin share page.",
            "Retry once later or provide the original video file. Do not claim video analysis ran.",
        )
    page = router.get("loaderData", {}).get("video_(id)/page", {})
    video_info = page.get("videoInfoRes", {})
    items = video_info.get("item_list", [])
    if not items:
        filter_list = video_info.get("filter_list") or []
        raise SkillRunError(
            "resolve_douyin_source",
            "The Douyin share page did not expose a public video item.",
            "Report the video as inaccessible and ask for an accessible link or original file. Do not invent a visual or spoken analysis.",
            {
                "aweme_id": aweme_id,
                "filter_list": filter_list,
                "notice": video_info.get("notice"),
                "detail_msg": video_info.get("detail_msg"),
            },
        )
    item = items[0]
    video = item.get("video", {}) or {}
    candidates = _iter_video_url_candidates(video)
    if not candidates:
        raise SkillRunError(
            "resolve_douyin_source",
            "No video URL candidates were present in Douyin router data.",
            "Ask for the original video file or a directly accessible media URL. Do not continue as if video understanding ran.",
            {"aweme_id": aweme_id},
        )

    # Follow redirects locally. Doubao often cannot connect to the aweme URL itself,
    # but can read a final CDN URL quickly when a candidate is still valid.
    play_source, aweme_play_url, final_cdn_url, content_length, resolve_attempts = _resolve_first_working_video_url(
        session, candidates, share_page, timeout, allow_unverified
    )

    return {
        "aweme_id": aweme_id,
        "share_page": share_page,
        "play_source": play_source,
        "aweme_play_url": aweme_play_url,
        "final_cdn_url": final_cdn_url,
        "content_length": content_length,
        "resolve_attempts": resolve_attempts,
        "item": item,
    }


def call_doubao(video_url: str, prompt: str, api_key: str, model: str, timeout: int, fps: float | None) -> tuple[dict[str, Any], dict[str, Any], float]:
    content: list[dict[str, Any]] = [{"type": "video_url", "video_url": {"url": video_url}}]
    if fps is not None:
        content[0]["video_url"]["fps"] = fps
    content.append({"type": "text", "text": prompt})
    req = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "thinking": {"type": "disabled"},
        "max_completion_tokens": 1800,
        "stream": False,
    }
    t0 = time.time()
    resp = requests.post(
        ARK_URL,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        data=json.dumps(req, ensure_ascii=False).encode("utf-8"),
        timeout=timeout,
    )
    elapsed = time.time() - t0
    try:
        obj = resp.json()
    except Exception:
        obj = {"raw": resp.text[:2000]}
    metrics = {
        "http": resp.status_code,
        "elapsed_sec": round(elapsed, 3),
        "request_bytes": len(json.dumps(req, ensure_ascii=False).encode("utf-8")),
        "response_bytes": len(resp.content),
    }
    return req, obj, metrics


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
    except SkillRunError as e:
        print(json.dumps(e.to_summary(), ensure_ascii=False, indent=2))
        sys.exit(1)
    except Exception as e:
        _print_error(
            "unexpected",
            f"{type(e).__name__}: {e}",
            "Inspect the input, credentials, and output directory. Do not claim analysis succeeded until a JSON summary has ok=true.",
        )
        sys.exit(1)


def run() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("url", help="Douyin share URL or video URL")
    ap.add_argument("--out-dir", default="/tmp/douyin-url-doubao-analysis")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--connect-timeout", type=int, default=30)
    ap.add_argument("--fps", type=float, default=0.5, help="Frames per second hint for Doubao URL reading; set <=0 to omit")
    ap.add_argument("--prompt", default=PROMPT)
    ap.add_argument("--allow-unverified-url", action="store_true", help="allow sending an unverified fallback Douyin media URL after all verification attempts fail")
    args = ap.parse_args()

    api_key = os.environ.get("DOUBAO_API_KEY") or os.environ.get("ARK_API_KEY")
    if not api_key:
        _print_error(
            "configuration",
            "missing DOUBAO_API_KEY/ARK_API_KEY",
            "Set DOUBAO_API_KEY or ARK_API_KEY in the environment before running video understanding.",
        )
        sys.exit(2)

    out_dir = Path(args.out_dir)
    _prepare_private_out_dir(out_dir)

    resolved = resolve_douyin(args.url, args.connect_timeout, args.allow_unverified_url)
    fps = args.fps if args.fps and args.fps > 0 else None
    req, obj, metrics = call_doubao(resolved["final_cdn_url"], args.prompt, api_key, args.model, args.timeout, fps)

    (out_dir / "meta.json").write_text(json.dumps(resolved["item"], ensure_ascii=False, indent=2), encoding="utf-8")
    safe_resolved = {k: v for k, v in resolved.items() if k != "item"}
    (out_dir / "resolved.json").write_text(json.dumps(safe_resolved, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "request.json").write_text(json.dumps(req, ensure_ascii=False), encoding="utf-8")
    (out_dir / "response.json").write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

    choice = obj.get("choices", [{}])[0] if isinstance(obj, dict) else {}
    msg = choice.get("message", {}) if isinstance(choice, dict) else {}
    item = resolved["item"]
    summary = {
        "ok": 200 <= metrics["http"] < 300 and bool(msg.get("content")) and not looks_like_false_success(msg.get("content", "")),
        **metrics,
        "aweme_id": resolved["aweme_id"],
        "author": item.get("author", {}).get("nickname"),
        "desc": item.get("desc"),
        "duration_sec": round((item.get("video", {}).get("duration") or 0) / 1000, 3),
        "statistics": item.get("statistics"),
        "url_chain": {
            "share_page": resolved["share_page"],
            "aweme_play_host": re.sub(r"^(https?://[^/]+).*$", r"\1", resolved["aweme_play_url"]),
            "final_cdn_host": re.sub(r"^(https?://[^/]+).*$", r"\1", resolved["final_cdn_url"]),
        },
        "usage": obj.get("usage") if isinstance(obj, dict) else None,
        "content": msg.get("content", ""),
        "error": obj.get("error") if isinstance(obj, dict) else None,
        "artifacts_private": True,
        "next_action": "Use this content as the video-understanding source." if msg.get("content") and not looks_like_false_success(msg.get("content", "")) else "Treat this as a failed or untrusted video_url attempt and fall back to Files API or ASR plus keyframes.",
        "paths": {
            "out_dir": str(out_dir),
            "meta": str(out_dir / "meta.json"),
            "resolved": str(out_dir / "resolved.json"),
            "response": str(out_dir / "response.json"),
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
