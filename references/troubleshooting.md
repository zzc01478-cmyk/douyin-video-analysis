# Troubleshooting

## Inaccessible Douyin Video

If router data contains no public `item_list`, report failure instead of analyzing. Include the input, resolved ID if available, any filter or notice fields, and what the user should provide next.

Use this response shape:

```json
{
  "ok": false,
  "stage": "resolve_douyin_source",
  "error": "The share page did not expose a public video item.",
  "next_action": "Ask for an accessible link or the original video file."
}
```

## Unverified Media URL

The scripts do not send unverified fallback URLs by default. Use `--allow-unverified-url` only when the user accepts that the URL may fail or may not be the intended media.

## Endpoint Confusion

`/api/coding/v3/chat/completions` is for the small `video_url` chat path. `/api/v3/files` and `/api/v3/responses` are for Files API and Responses API. A 404 may mean the endpoint base was mixed, not that the media URL is bad.

## False Success

Treat a response as untrusted when it says the video was not provided, asks for the video again, or gives a generic template without source-specific visual or spoken details. Fall back to Files API, proxy video, ASR, or keyframes.

## Upload Or Processing Failure

If Files API upload fails, note whether the failure occurred during upload, file preprocessing, or Responses inference. If the original file is large or high-resolution, create a proxy and retry once. If proxy retry fails, switch to ASR plus keyframes.

## Artifact Privacy

Output directories can contain private CDN URLs, full responses, file IDs, downloaded media, proxy media, redacted or raw requests, transcripts, and frames. Do not commit or share task output folders. Delete them after use when the analysis is complete.
