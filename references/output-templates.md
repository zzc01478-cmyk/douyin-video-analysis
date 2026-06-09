# Output Templates

## Lightweight Summary

```text
Source: <video_url | file_id | proxy | ASR+keyframes | image/page | failure metadata>

This video is about: ...
Core visual/speech points: ...
One-line judgment: ...
Confidence and limits: ...
```

## Full Teardown

```text
Source: <actual analysis route>

1. Visual sequence
2. Speech/captions
3. First-three-second hook
4. Content structure
5. Most reusable pattern
6. Uncertainty notes
```

## Note/Image Analysis

```text
Source: page-visible metadata + image/screenshot analysis. No video understanding was run.

Visible page facts: ...
Image observations: ...
What can be inferred: ...
What cannot be inferred: ...
```

## Failure Report

```text
Source: failure metadata only. No video understanding was run.

Original input: ...
Resolved ID if available: ...
Failed stage: ...
Reason: ...
Media obtained: yes/no
Next action: provide an accessible link, original video file, screenshot, or transcript.
```
