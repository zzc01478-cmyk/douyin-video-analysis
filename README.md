# Douyin Video Analysis Skill

A reusable Codex skill for analyzing Douyin share links, public video URLs, and local video files through Ark/Doubao video understanding, with safer fallbacks for large files, unavailable links, ASR, and keyframe-based review.

## What It Does

- Resolves Douyin share links into real media sources when possible.
- Routes small videos to direct video understanding.
- Routes larger videos through Ark Files API and Responses API.
- Creates lower-resolution proxy videos when files are too large or high-resolution processing stalls.
- Falls back to ASR plus keyframes when native video understanding fails or is not needed.
- Reports the actual evidence source instead of pretending a video was analyzed.

## Project Layout

```text
.
├── SKILL.md
├── references/
│   ├── api-paths.md
│   ├── asr-and-frames.md
│   ├── note-image-fallback.md
│   ├── output-templates.md
│   └── troubleshooting.md
└── scripts/
    ├── ark_files_responses_video_analyze.py
    ├── doubao_video_analyze.py
    └── douyin_url_doubao_analyze.py
```

## Requirements

- Python 3
- Python package: `requests`
- `ffmpeg` and `ffprobe`
- An Ark/Doubao-compatible API key in `ARK_API_KEY` or `DOUBAO_API_KEY`

For ASR fallback, set `SILI_FLOW_API_KEY` if you use the SiliconFlow example in `references/asr-and-frames.md`.

## Quick Start

```bash
python3 scripts/ark_files_responses_video_analyze.py "<douyin-link-or-video-path>" \
  --out-dir /tmp/douyin-skill-run \
  --fps 0.3
```

Resolve only, without calling video understanding:

```bash
python3 scripts/ark_files_responses_video_analyze.py "<douyin-link-or-video-path>" \
  --out-dir /tmp/douyin-skill-resolve \
  --resolve-only
```

## Privacy Notes

Output directories can contain private URLs, responses, file IDs, downloaded video, proxy video, transcripts, or extracted frames. Do not commit or share task output folders.

This repository intentionally excludes private case studies, account strategy, internal paths, and real historical task outputs.

## License

MIT
