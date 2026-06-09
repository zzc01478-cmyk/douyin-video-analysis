# 抖音视频分析 Skill

这是一个给 Codex 用的短视频分析 skill。你给它一条抖音链接、一个公开视频地址，或者一个本地视频文件，它会先把能拿到的视频源找出来，再按视频大小选择合适的分析方式。

它不会假装自己看过视频。链接失效、作品不可见、模型返回空话，这些情况都会被当成失败处理，然后告诉你下一步该换链接、给原片，还是改用转写和截图。

## 它能做什么

- 解析抖音分享链接，尽量找到真实可访问的视频地址。
- 小视频直接交给 Ark/Doubao 做视频理解。
- 大一点的视频走 Ark Files API 和 Responses API。
- 文件太大、分辨率太高或处理失败时，先压一个较小的代理视频再试。
- 豆包看不了，或者只需要口播内容时，退到语音转文字加关键帧。
- 每次结果都说明依据是什么：公开视频地址、上传后的文件、代理视频、转写文本、关键帧，还是失败信息。

## 目录结构

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

## 运行前准备

本地需要这些东西：

- Python 3
- Python 包：`requests`
- `ffmpeg` 和 `ffprobe`
- Ark/Doubao 可用的 API key，放在 `ARK_API_KEY` 或 `DOUBAO_API_KEY`

如果你要用语音转文字兜底，可以再配置 `SILI_FLOW_API_KEY`。相关示例在 `references/asr-and-frames.md` 里。

不要把 key 写进代码，也不要把 `.env` 文件提交到仓库。

## 最常用的命令

分析一条抖音链接或一个本地视频：

```bash
python3 scripts/ark_files_responses_video_analyze.py "<抖音链接或本地视频路径>" \
  --out-dir /tmp/douyin-skill-run \
  --fps 0.3
```

只解析视频来源，不调用模型：

```bash
python3 scripts/ark_files_responses_video_analyze.py "<抖音链接或本地视频路径>" \
  --out-dir /tmp/douyin-skill-resolve \
  --resolve-only
```

如果你只是想跑小视频的旧路线，可以看 `scripts/doubao_video_analyze.py`。不过一般情况下，优先用 `ark_files_responses_video_analyze.py` 就够了。

## 输出目录别乱传

`--out-dir` 里面可能会有私有信息，比如：

- 真实视频地址
- 模型返回结果
- 上传后的文件 ID
- 下载下来的视频
- 压缩后的代理视频
- 转写文本
- 抽出来的关键帧

这些目录只适合自己调试，不适合提交到 GitHub，也不适合直接发给别人。

这个公开仓库只保留通用的视频处理链路。个人案例、账号策略、内部路径和历史运行产物都没有放进来。

## 许可证

MIT
