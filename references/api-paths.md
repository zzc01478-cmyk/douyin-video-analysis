# API Paths And Runtime Settings

## Runtime

Required:

- `python3`
- Python package: `requests`
- `ffmpeg` and `ffprobe` for proxy video, duration checks, audio extraction, and keyframes

Credentials:

- `ARK_API_KEY` is preferred for Files API and Responses API.
- `DOUBAO_API_KEY` can be used by the helper scripts when it is the available Ark-compatible key.
- `DOUBAO_VIDEO_MODEL` overrides the default model.

Do not print credential values. Only check whether the variable is present.

## Endpoints

Small `video_url` chat-completions path:

```text
https://ark.cn-beijing.volces.com/api/coding/v3/chat/completions
```

Files API and Responses API base:

```text
https://ark.cn-beijing.volces.com/api/v3
```

The two bases are intentionally different. Do not send Files API requests to the chat-completions base.

## Size Routing

- `<=50MiB`: use a public `video_url` when the URL is reachable by Ark/Doubao.
- `>50MiB` and `<=512MiB`: download or use the local video, upload via Files API, then call Responses with `input_video.file_id`.
- `>512MiB`: create a lower-resolution proxy first.
- High-resolution input that stalls during preprocessing: proxy to 720p or below and retry.

## Request Shapes

Chat-completions `video_url` content:

```json
{
  "model": "doubao-seed-2-0-pro-260215",
  "messages": [{
    "role": "user",
    "content": [
      {"type": "video_url", "video_url": {"url": "<public-or-data-url>", "fps": 0.3}},
      {"type": "text", "text": "<analysis prompt>"}
    ]
  }],
  "thinking": {"type": "disabled"},
  "stream": false
}
```

Responses API file content:

```json
{
  "model": "doubao-seed-2-0-pro-260215",
  "input": [{
    "role": "user",
    "content": [
      {"type": "input_video", "file_id": "<file-id-from-files-api>"},
      {"type": "input_text", "text": "<analysis prompt>"}
    ]
  }]
}
```

`file_id` must come from a successful Files API upload. Do not invent it from user text or reuse IDs from unrelated runs.
