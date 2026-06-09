#!/usr/bin/env python3
"""Ark/Doubao video understanding via official Files API + Responses API.

Use for videos that exceed the normal video_url/Base64 50MiB path, or when you
want explicit file_id based processing. It accepts a local MP4/MOV/AVI file or a
public video URL. For Douyin share URLs it resolves iesdouyin SSR metadata to the
final douyinvod CDN URL first.

Secrets are read from env and never printed.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

MI_B = 1024 * 1024
MOBILE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148"
BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
DEFAULT_MODEL = os.environ.get("DOUBAO_VIDEO_MODEL", "doubao-seed-2-0-pro-260215")
DEFAULT_ARK_BASE = os.environ.get("ARK_BASE_URL") or os.environ.get("VOLCENGINE_ARK_BASE_URL") or "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_CHAT_BASE = os.environ.get("DOUBAO_BASE_URL", "https://ark.cn-beijing.volces.com/api/coding/v3")

PROMPT = """直接输出最终答案，不要展开思考过程。请用中文分析这条视频：
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


def require_api_key(api_key: str | None) -> str:
    if api_key:
        return api_key
    _print_error(
        "configuration",
        "missing ARK_API_KEY/DOUBAO_API_KEY",
        "Set ARK_API_KEY or DOUBAO_API_KEY in the environment before running video understanding.",
    )
    sys.exit(2)


def _endpoint(base: str, suffix: str) -> str:
    base = base.rstrip("/")
    return base if base.endswith(suffix) else base + suffix


def _json_response(resp: requests.Response) -> dict[str, Any]:
    try:
        return resp.json()
    except Exception:
        return {"raw": resp.text[:3000]}


def _run(cmd: list[str], timeout: int | None = None) -> str:
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd)}\n{p.stderr[-1500:]}")
    return p.stdout


def _is_url(s: str) -> bool:
    return s.startswith("http://") or s.startswith("https://")


def _media_headers(referer: str | None = None) -> dict[str, str]:
    headers = {"User-Agent": BROWSER_UA}
    if referer:
        headers["Referer"] = referer
    return headers


def _media_referer(url: str) -> str | None:
    host = urlparse(url).netloc.lower()
    if any(marker in host for marker in ("douyinvod.com", "zjcdn.com", "pstatp.com", "snssdk.com", "douyin.com")):
        return "https://www.douyin.com/"
    return None


def _is_douyin_share(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if "douyinvod.com" in host:
        return False
    if "snssdk.com" in host and "/aweme/v1/play" in parsed.path:
        return False
    return any(x in host for x in ["douyin.com", "iesdouyin.com", "snssdk.com"])


def _extract_aweme_id(text: str, final_url: str) -> str | None:
    candidates = re.findall(r"/(?:video|note)/(\d+)", final_url + "\n" + text[:5000])
    return candidates[0] if candidates else None


def _load_router_data(html: str) -> dict[str, Any] | None:
    m = re.search(r"<script>window\._ROUTER_DATA\s*=\s*(\{.*?\})</script>", html, re.S)
    if not m:
        return None
    return json.loads(m.group(1))


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
                headers=_media_headers(referer),
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

    share_page = f"https://www.iesdouyin.com/share/video/{aweme_id}/"
    r2 = session.get(share_page, headers=headers, timeout=timeout)
    r2.raise_for_status()
    router = _load_router_data(r2.text)
    if not router:
        raise RuntimeError("could not parse window._ROUTER_DATA from iesdouyin share page")
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
    play_source, aweme_play_url, final_cdn_url, content_length, resolve_attempts = _resolve_first_working_video_url(
        session, candidates, share_page, timeout, allow_unverified
    )
    return {
        "kind": "douyin",
        "aweme_id": aweme_id,
        "share_page": share_page,
        "play_source": play_source,
        "aweme_play_url": aweme_play_url,
        "final_cdn_url": final_cdn_url,
        "content_length": content_length,
        "resolve_attempts": resolve_attempts,
        "item": item,
    }


def _parse_int(v: str | None) -> int | None:
    if not v:
        return None
    try:
        return int(v)
    except Exception:
        return None


def content_length(url: str, timeout: int, referer: str | None = None) -> int | None:
    headers = _media_headers(referer or _media_referer(url))
    for method in ("head", "get"):
        try:
            if method == "head":
                r = requests.head(url, headers=headers, allow_redirects=True, timeout=timeout)
            else:
                r = requests.get(url, headers=headers, allow_redirects=True, timeout=timeout, stream=True)
            if 200 <= r.status_code < 400:
                n = _parse_int(r.headers.get("Content-Length"))
                r.close()
                if n is not None:
                    return n
            r.close()
        except Exception:
            continue
    return None


def download_url(url: str, out_dir: Path, filename: str | None, timeout: int, referer: str | None = None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    if not filename:
        path_name = Path(urlparse(url).path).name or "video.mp4"
        filename = re.sub(r"[^A-Za-z0-9._-]+", "_", path_name)[:120] or "video.mp4"
        if "." not in filename:
            filename += ".mp4"
    dst = out_dir / filename
    headers = _media_headers(referer or _media_referer(url))
    with requests.get(url, headers=headers, allow_redirects=True, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        with dst.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
    return dst


def ffprobe_resolution(path: Path) -> tuple[int, int] | None:
    if not shutil.which("ffprobe"):
        return None
    try:
        out = _run([
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", str(path)
        ], timeout=30).strip()
        if "x" in out:
            w, h = out.split("x", 1)
            return int(w), int(h)
    except Exception:
        return None
    return None


def make_proxy(src: Path, out_dir: Path, height: int, crf: int, no_audio: bool) -> Path:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found; cannot create proxy video")
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"proxy_h{height}_crf{crf}" + ("_noaudio" if no_audio else "")
    dst = out_dir / f"{src.stem}_{suffix}.mp4"
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
        "-vf", f"scale=-2:{height}", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
    ]
    if no_audio:
        cmd += ["-an"]
    else:
        cmd += ["-c:a", "aac", "-b:a", "48k"]
    cmd.append(str(dst))
    _run(cmd, timeout=1800)
    return dst


def extract_chat_content(obj: dict[str, Any]) -> str:
    try:
        return obj.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
    except Exception:
        return ""


def extract_responses_text(obj: dict[str, Any]) -> str:
    if isinstance(obj.get("output_text"), str):
        return obj["output_text"]
    texts: list[str] = []
    for item in obj.get("output", []) or []:
        if isinstance(item, dict):
            for c in item.get("content", []) or []:
                if isinstance(c, dict):
                    if c.get("type") in {"output_text", "text"} and c.get("text"):
                        texts.append(str(c.get("text")))
                    elif c.get("type") == "refusal" and c.get("refusal"):
                        texts.append(str(c.get("refusal")))
    return "\n".join(texts)


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


def call_chat_video_url(url: str, prompt: str, api_key: str, model: str, chat_base: str, timeout: int, fps: float | None) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    endpoint = _endpoint(chat_base, "/chat/completions")
    video_obj: dict[str, Any] = {"url": url}
    if fps and fps > 0:
        video_obj["fps"] = fps
    req = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "video_url", "video_url": video_obj},
            {"type": "text", "text": prompt},
        ]}],
        "thinking": {"type": "disabled"},
        "max_completion_tokens": 1800,
        "stream": False,
    }
    t0 = time.time()
    resp = requests.post(
        endpoint,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        data=json.dumps(req, ensure_ascii=False).encode("utf-8"),
        timeout=timeout,
    )
    obj = _json_response(resp)
    metrics = {
        "http": resp.status_code,
        "elapsed_sec": round(time.time() - t0, 3),
        "request_bytes": len(json.dumps(req, ensure_ascii=False).encode("utf-8")),
        "response_bytes": len(resp.content),
    }
    return req, obj, metrics


def call_responses_video_url(url: str, prompt: str, api_key: str, model: str, ark_base: str, timeout: int, fps: float | None) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    endpoint = _endpoint(ark_base, "/responses")
    video_obj: dict[str, Any] = {"type": "input_video", "video_url": url}
    if fps and fps > 0:
        video_obj["fps"] = fps
    req = {
        "model": model,
        "input": [{"role": "user", "content": [
            video_obj,
            {"type": "input_text", "text": prompt},
        ]}],
        "stream": False,
    }
    t0 = time.time()
    resp = requests.post(
        endpoint,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        data=json.dumps(req, ensure_ascii=False).encode("utf-8"),
        timeout=timeout,
    )
    obj = _json_response(resp)
    metrics = {
        "http": resp.status_code,
        "elapsed_sec": round(time.time() - t0, 3),
        "request_bytes": len(json.dumps(req, ensure_ascii=False).encode("utf-8")),
        "response_bytes": len(resp.content),
    }
    return req, obj, metrics


def upload_file(path: Path, api_key: str, ark_base: str, fps: float, timeout: int) -> tuple[dict[str, Any], dict[str, Any]]:
    endpoint = _endpoint(ark_base, "/files")
    mime = mimetypes.guess_type(str(path))[0] or "video/mp4"
    data = {"purpose": "user_data", "preprocess_configs[video][fps]": str(fps)}
    t0 = time.time()
    with path.open("rb") as fh:
        resp = requests.post(
            endpoint,
            headers={"Authorization": f"Bearer {api_key}"},
            data=data,
            files={"file": (path.name, fh, mime)},
            timeout=timeout,
        )
    obj = _json_response(resp)
    metrics = {"http": resp.status_code, "elapsed_sec": round(time.time() - t0, 3), "sent_video_bytes": path.stat().st_size}
    if not (200 <= resp.status_code < 300) or not obj.get("id"):
        raise RuntimeError(f"Files API upload failed: http={resp.status_code} body={json.dumps(obj, ensure_ascii=False)[:1000]}")
    return obj, metrics


def wait_file_active(file_id: str, api_key: str, ark_base: str, timeout: int, interval: float) -> dict[str, Any]:
    endpoint = _endpoint(ark_base, f"/files/{file_id}")
    deadline = time.time() + timeout
    last: dict[str, Any] = {}
    while time.time() < deadline:
        resp = requests.get(endpoint, headers={"Authorization": f"Bearer {api_key}"}, timeout=60)
        obj = _json_response(resp)
        last = obj
        status = str(obj.get("status", "")).lower()
        if status == "active":
            return obj
        if status in {"failed", "error", "expired", "deleted"}:
            raise RuntimeError(f"file processing failed: {json.dumps(obj, ensure_ascii=False)[:1000]}")
        time.sleep(interval)
    raise TimeoutError(f"file did not become active in {timeout}s; last={json.dumps(last, ensure_ascii=False)[:1000]}")


def call_responses_file(file_id: str, prompt: str, api_key: str, model: str, ark_base: str, timeout: int) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    endpoint = _endpoint(ark_base, "/responses")
    req = {
        "model": model,
        "input": [{"role": "user", "content": [
            {"type": "input_video", "file_id": file_id},
            {"type": "input_text", "text": prompt},
        ]}],
        "stream": False,
    }
    t0 = time.time()
    resp = requests.post(
        endpoint,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        data=json.dumps(req, ensure_ascii=False).encode("utf-8"),
        timeout=timeout,
    )
    obj = _json_response(resp)
    metrics = {"http": resp.status_code, "elapsed_sec": round(time.time() - t0, 3), "response_bytes": len(resp.content)}
    return req, obj, metrics


def analyze_via_files(path: Path, args: argparse.Namespace, api_key: str) -> dict[str, Any]:
    upload_obj, upload_metrics = upload_file(path, api_key, args.ark_base, args.fps, args.upload_timeout)
    file_id = upload_obj["id"]
    active_obj = wait_file_active(file_id, api_key, args.ark_base, args.process_timeout, args.poll_interval)
    req, obj, resp_metrics = call_responses_file(file_id, args.prompt, api_key, args.model, args.ark_base, args.response_timeout)
    return {
        "mode": "files_api_responses",
        "file_id": file_id,
        "upload": upload_obj,
        "upload_metrics": upload_metrics,
        "active": active_obj,
        "request": req,
        "response": obj,
        "response_metrics": resp_metrics,
        "content": extract_responses_text(obj),
        "ok": 200 <= resp_metrics["http"] < 300 and bool(extract_responses_text(obj)),
    }


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
    ap = argparse.ArgumentParser(description="Doubao/Ark video understanding: video_url <=50MiB, Files API + Responses for larger local videos")
    ap.add_argument("source", help="local video path, public video URL, or Douyin share URL")
    ap.add_argument("--out-dir", default="/tmp/ark-video-understand")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--ark-base", default=DEFAULT_ARK_BASE.rstrip("/"), help="official Ark base for /files and /responses")
    ap.add_argument("--chat-base", default=DEFAULT_CHAT_BASE.rstrip("/"), help="base for small video_url chat completions")
    ap.add_argument("--fps", type=float, default=0.3, help="sampling fps; long talking videos usually 0.2-0.5")
    ap.add_argument("--max-url-mib", type=float, default=50.0, help="use video_url only when remote Content-Length is <= this")
    ap.add_argument("--max-files-mib", type=float, default=512.0, help="Files API file size limit before proxying")
    ap.add_argument("--proxy-height", type=int, default=720)
    ap.add_argument("--proxy-crf", type=int, default=28)
    ap.add_argument("--keep-audio-proxy", action="store_true", help="keep audio in proxy; default removes audio")
    ap.add_argument("--force-files", action="store_true", help="force downloading URL and using Files API even when <=50MiB")
    ap.add_argument("--prefer-files", action="store_true", help="download URL and use Files API when remote size is unknown instead of trying video_url")
    ap.add_argument("--force-proxy", action="store_true", help="force creating a 720p proxy before Files API")
    ap.add_argument("--auto-proxy-highres", action="store_true", help="proxy first when ffprobe sees width>=1920 or height>=1080")
    ap.add_argument("--retry-proxy", action="store_true", default=True, help="retry with proxy if original Files API path fails")
    ap.add_argument("--no-retry-proxy", dest="retry_proxy", action="store_false")
    ap.add_argument("--connect-timeout", type=int, default=30)
    ap.add_argument("--download-timeout", type=int, default=900)
    ap.add_argument("--upload-timeout", type=int, default=1200)
    ap.add_argument("--process-timeout", type=int, default=600)
    ap.add_argument("--response-timeout", type=int, default=300)
    ap.add_argument("--small-url-api", choices=["chat", "responses"], default="chat", help="API for <=50MiB public URL: chat keeps legacy tested path; responses uses official /api/v3/responses input_video.video_url")
    ap.add_argument("--poll-interval", type=float, default=2.0)
    ap.add_argument("--prompt", default=PROMPT)
    ap.add_argument("--resolve-only", action="store_true", help="only resolve Douyin/source URL metadata and write resolved.json")
    ap.add_argument("--allow-unverified-url", action="store_true", help="allow sending an unverified fallback Douyin media URL after all verification attempts fail")
    args = ap.parse_args()

    api_key = os.environ.get("ARK_API_KEY") or os.environ.get("DOUBAO_API_KEY")

    out_dir = Path(args.out_dir)
    _prepare_private_out_dir(out_dir)
    source = args.source
    source_info: dict[str, Any] = {"input": source}
    local_path: Path | None = None

    if _is_url(source):
        if _is_douyin_share(source):
            resolved = resolve_douyin(source, args.connect_timeout, args.allow_unverified_url)
            item = resolved.get("item", {})
            url = resolved["final_cdn_url"]
            n = resolved.get("content_length") or content_length(url, args.connect_timeout, "https://www.douyin.com/")
            source_info.update({k: v for k, v in resolved.items() if k != "item"})
            source_info["content_length"] = n
            source_info["author"] = item.get("author", {}).get("nickname")
            source_info["desc"] = item.get("desc")
            source_info["duration_sec"] = round((item.get("video", {}).get("duration") or 0) / 1000, 3)
            filename = f"{resolved.get('aweme_id') or 'douyin'}.mp4"
        else:
            url = source
            n = content_length(url, args.connect_timeout)
            source_info.update({"kind": "url", "final_cdn_url": url, "content_length": n})
            filename = None

        (out_dir / "resolved.json").write_text(json.dumps(source_info, ensure_ascii=False, indent=2), encoding="utf-8")
        if args.resolve_only:
            print(json.dumps({
                "ok": True,
                "mode": "resolve_only",
                "source": source_info,
                "artifacts_private": True,
                "next_action": "Use content_length and source kind to choose video_url, Files API, note analysis, or a failure report.",
                "paths": {"out_dir": str(out_dir), "resolved": str(out_dir / "resolved.json")},
            }, ensure_ascii=False, indent=2))
            return
        api_key = require_api_key(api_key)
        if (not args.force_files) and (not args.prefer_files) and (n is None or n <= args.max_url_mib * MI_B):
            try:
                if args.small_url_api == "responses":
                    req, obj, metrics = call_responses_video_url(url, args.prompt, api_key, args.model, args.ark_base, args.response_timeout, args.fps)
                    content = extract_responses_text(obj)
                    mode = "video_url_responses"
                else:
                    req, obj, metrics = call_chat_video_url(url, args.prompt, api_key, args.model, args.chat_base, args.response_timeout, args.fps)
                    content = extract_chat_content(obj)
                    mode = "video_url_chat"
            except Exception as e:
                source_info["video_url_error"] = str(e)
            else:
                (out_dir / "request.json").write_text(json.dumps(req, ensure_ascii=False), encoding="utf-8")
                (out_dir / "response.json").write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
                summary = {
                    "ok": 200 <= metrics["http"] < 300 and bool(content) and not looks_like_false_success(content),
                    "mode": mode,
                    "source": source_info,
                    "metrics": metrics,
                    "usage": obj.get("usage") if isinstance(obj, dict) else None,
                    "content": content,
                    "error": obj.get("error") if isinstance(obj, dict) else None,
                    "artifacts_private": True,
                    "next_action": "Use this content as the video-understanding source." if content and not looks_like_false_success(content) else "Treat this as a failed video_url attempt and fall back to local download plus Files API or ASR.",
                    "paths": {"out_dir": str(out_dir), "response": str(out_dir / "response.json")},
                }
                (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
                if summary["ok"]:
                    print(json.dumps(summary, ensure_ascii=False, indent=2))
                    return
                source_info["video_url_error"] = json.dumps({
                    "http": metrics.get("http"),
                    "error": summary.get("error"),
                    "content_preview": content[:500],
                }, ensure_ascii=False)

        local_path = download_url(url, out_dir / "download", filename, args.download_timeout)
    else:
        local_path = Path(source).expanduser().resolve()
        if not local_path.exists():
            raise FileNotFoundError(str(local_path))
        source_info.update({"kind": "local", "path": str(local_path), "content_length": local_path.stat().st_size})
        (out_dir / "resolved.json").write_text(json.dumps(source_info, ensure_ascii=False, indent=2), encoding="utf-8")
        if args.resolve_only:
            print(json.dumps({
                "ok": True,
                "mode": "resolve_only",
                "source": source_info,
                "artifacts_private": True,
                "next_action": "Use content_length and file metadata to choose Files API, proxy video, or ASR plus keyframes.",
                "paths": {"out_dir": str(out_dir), "resolved": str(out_dir / "resolved.json")},
            }, ensure_ascii=False, indent=2))
            return

    assert local_path is not None
    api_key = require_api_key(api_key)
    send_path = local_path
    resolution = ffprobe_resolution(send_path)
    should_proxy = args.force_proxy or send_path.stat().st_size > args.max_files_mib * MI_B
    if args.auto_proxy_highres and resolution and (resolution[0] >= 1920 or resolution[1] >= 1080):
        should_proxy = True
    if should_proxy:
        send_path = make_proxy(local_path, out_dir / "proxy", args.proxy_height, args.proxy_crf, no_audio=not args.keep_audio_proxy)

    try:
        result = analyze_via_files(send_path, args, api_key)
    except Exception as e:
        if args.retry_proxy and send_path == local_path:
            proxy_path = make_proxy(local_path, out_dir / "proxy", args.proxy_height, args.proxy_crf, no_audio=not args.keep_audio_proxy)
            result = analyze_via_files(proxy_path, args, api_key)
            result["retry_after_error"] = str(e)
            send_path = proxy_path
        else:
            raise

    (out_dir / "upload.json").write_text(json.dumps(result.get("upload"), ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "active.json").write_text(json.dumps(result.get("active"), ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "request.json").write_text(json.dumps(result.get("request"), ensure_ascii=False), encoding="utf-8")
    (out_dir / "response.json").write_text(json.dumps(result.get("response"), ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "ok": result["ok"] and not looks_like_false_success(result.get("content", "")),
        "mode": result["mode"],
        "source": source_info,
        "local_video": str(local_path),
        "sent_video": str(send_path),
        "source_bytes": local_path.stat().st_size,
        "sent_video_bytes": send_path.stat().st_size,
        "resolution": resolution,
        "file_id": result.get("file_id"),
        "upload_metrics": result.get("upload_metrics"),
        "response_metrics": result.get("response_metrics"),
        "usage": result.get("response", {}).get("usage") if isinstance(result.get("response"), dict) else None,
        "content": result.get("content", ""),
        "error": result.get("response", {}).get("error") if isinstance(result.get("response"), dict) else None,
        "retry_after_error": result.get("retry_after_error"),
        "artifacts_private": True,
        "next_action": "Use this content as the video-understanding source." if result["ok"] and result.get("content") and not looks_like_false_success(result.get("content", "")) else "Treat this as failed or untrusted and fall back to ASR plus keyframes.",
        "paths": {"out_dir": str(out_dir), "response": str(out_dir / "response.json")},
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
