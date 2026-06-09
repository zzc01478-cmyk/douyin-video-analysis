---
name: douyin-video-analysis
description: "Analyze Douyin share links, public video URLs, and local video files with a clear routing flow: resolve the media source, use Ark/Doubao native video understanding when possible, fall back to Files API, proxy video, ASR, and keyframes when needed, and always report the actual evidence source."
---

# Douyin Video Analysis

Use this skill when the user wants to analyze a Douyin short video, a public video URL, a local video file, or a Douyin image/note page. Keep the skill focused on the reusable media-analysis workflow; do not mix in user-specific case studies, account strategy, or private knowledge-base actions.

## Core Flow

Choose the path by input type:

1. Douyin share link: resolve the share page, parse router metadata, find verified media URL candidates, and follow redirects locally to the final CDN URL.
2. Public video URL: check reachability and content length before choosing the analysis route.
3. Local video file: verify the file exists, then analyze directly through Files API or create a smaller proxy if needed.
4. Douyin note/image page: do not claim video understanding ran; use page-visible metadata and image analysis instead.

Choose the analysis route by size and availability:

- `<=50MiB`: prefer `video_url` through Ark/Doubao.
- `>50MiB` and `<=512MiB`: download or use the local file, upload through Ark Files API, wait for `active`, then call Responses with `input_video.file_id`.
- `>512MiB`, high-resolution preprocessing timeouts, or failed Files API attempts: create a lower-resolution proxy, then retry Files API.
- If native video understanding fails, output is generic, only speech is needed, or cost is the priority: use ASR plus keyframe extraction.

Always report the actual evidence source: `video_url`, Files API `file_id`, proxy video, ASR transcript, keyframes, screenshots, image analysis, or failure metadata.

## Atomic Script Interfaces

Use `python3`, not `python`, in commands.

### `scripts/ark_files_responses_video_analyze.py`

Main runner for Douyin links, public URLs, and local files. It resolves source metadata, routes small public videos to `video_url`, routes larger files to Files API + Responses, and can proxy oversized or high-resolution videos.

```bash
python3 scripts/ark_files_responses_video_analyze.py "<douyin-link-or-video-path>" \
  --out-dir /tmp/douyin-skill-run \
  --fps 0.3
```

Key parameters:

- `source`: user-provided link or local file path. Do not invent or reconstruct it.
- `--out-dir`: task-local directory. It may contain private URLs, responses, file IDs, downloaded video, or proxy video.
- `--fps`: sampling hint. Use `0.2-0.5` for talking-head or course videos; raise only when visual details change quickly.
- `--resolve-only`: only resolve source metadata and write `resolved.json`.
- `--allow-unverified-url`: off by default. Use only when the user explicitly accepts the risk of sending an unverified fallback URL.

Success content should state the mode and next action. Failure content must state the failed stage, reason, and what to do next.

### `scripts/douyin_url_doubao_analyze.py`

Smaller URL-only helper for Douyin links that should go directly from final CDN URL to Ark/Doubao `video_url`.

Use it only when the video is expected to fit the small `video_url` route. For large or unknown-size media, use the main runner.

### `scripts/doubao_video_analyze.py`

Legacy local-file helper that base64-encodes a local file and sends it through chat-completions `video_url`.

Prefer the main runner for public sharing. This helper is useful only for small local test videos or environments where Files API is unavailable. It saves only a redacted request by default; use `--save-raw-artifacts` only for private debugging.

## Trust Checks

Treat HTTP success as only transport success. Mark the result as failed or untrusted if the model says the video was not provided, asks for a video again, gives a generic template, or fails to mention visible or spoken facts from the source.

If Douyin router data reports no public `item_list`, privacy filtering, deletion, or another inaccessible state, produce a failure report. Do not infer visuals, speech, captions, hooks, or structure without a media source.

If the input is a note/image page, clearly say it is image/page analysis, not video understanding.

## Output Modes

Pick the smallest useful output:

- Lightweight summary: what the video says, key visual/speech points, one-line judgment, and evidence source.
- Full teardown: visual sequence, speech/captions, first-three-second hook, structure, reusable pattern, and uncertainty notes.
- Note/image analysis: visible page facts, image observations, and limits.
- Failure report: original input, resolved ID when available, exact failed stage, whether media was obtained, and what the user should provide next.

## References

- `references/api-paths.md`: endpoints, environment variables, thresholds, and request shapes.
- `references/troubleshooting.md`: inaccessible videos, endpoint confusion, false success, upload failures, and artifact privacy.
- `references/output-templates.md`: concise response templates.
- `references/asr-and-frames.md`: ASR and keyframe fallback commands.
- `references/note-image-fallback.md`: Douyin note/image handling.

## Safety And Packaging

Never include task output directories in a shared package. Exclude `request.json`, `response.json`, `summary.json`, `resolved.json`, `upload.json`, `active.json`, downloaded videos, proxy videos, transcripts, extracted frames, caches, and temp folders.

Keep credentials in environment variables only. Do not echo key values, write `.env` files into the package, or paste authorization headers into documentation with real secrets.
