# ASR And Keyframe Fallback

Use ASR plus keyframes when native video understanding fails, when only speech is needed, or when batch cost matters more than frame-level understanding.

## Extract Audio

```bash
ffmpeg -hide_banner -loglevel error -i input.mp4 -vn -ac 1 -ar 16000 -y output.wav
```

## SiliconFlow ASR

Use `SILI_FLOW_API_KEY` from the environment. Check presence only; do not print the value.

```bash
test -n "${SILI_FLOW_API_KEY:-}" || { echo "missing SILI_FLOW_API_KEY"; exit 2; }
curl -sS -X POST "https://api.siliconflow.cn/v1/audio/transcriptions" \
  -H "Authorization: Bearer $SILI_FLOW_API_KEY" \
  -F "file=@output.wav" \
  -F "model=FunAudioLLM/SenseVoiceSmall" \
  > transcription.json
```

Read text:

```bash
python3 - <<'PY'
import json
obj = json.load(open("transcription.json"))
print(obj.get("text", ""))
PY
```

## Extract Keyframes

```bash
mkdir -p frames
ffmpeg -hide_banner -loglevel error -i input.mp4 \
  -vf "fps=1/3,scale=360:-1" -q:v 3 \
  frames/frame_%03d.jpg
```

When reporting, clearly say the result is based on ASR plus keyframes, not full native video understanding.
