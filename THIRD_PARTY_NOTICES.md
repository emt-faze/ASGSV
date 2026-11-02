# 第三方来源与许可

第三方运行依赖通过安装脚本下载。推荐发布 ZIP 附带四个 RapidOCR ONNX 模型，源码包不附带 OCR 模型；两个包均附带约 1.25 MB 的 Silero VAD 音频边界模型及其 MIT 许可；TTS 模型权重和个人参考录音均不包含。OCR 模型的固定版本、SHA-256 和 Apache-2.0 许可，以及原有游戏界面模板的来源与 GPL 许可，见 nte-dialogue-ocr/THIRD_PARTY_NOTICES.md 和 assets 中的许可证文件。

| 组件 | 官方来源 | 许可说明 |
| --- | --- | --- |
| Qwen3-TTS 代码 | https://github.com/QwenLM/Qwen3-TTS | 上游 Apache-2.0 |
| Qwen3-TTS-12Hz-0.6B-Base | https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-Base | 模型卡标注 Apache-2.0，固定版本见安装说明 |
| PyTorch | https://github.com/pytorch/pytorch | 以安装版本 LICENSE/NOTICE 为准 |
| PySide6 / Qt | https://www.qt.io/qt-for-python | LGPL/GPL/商业许可依组件及使用方式而定 |
| RapidOCR | https://github.com/RapidAI/RapidOCR | 以所安装版本及其模型许可证为准 |
| imageio-ffmpeg / FFmpeg | https://github.com/imageio/imageio-ffmpeg | Python 封装和 FFmpeg 二进制各自适用其许可证 |
| 其他依赖 | 各软件包发布页 | 依赖清单见 pyproject.toml 和 requirements 文件 |

本次发布没有替项目另行选择开源许可证；第三方许可证不自动适用于本仓库全部代码，也不覆盖游戏参考素材。再分发依赖或录音时请遵循相应来源的许可。

## 音频整理与可选转写

- `nte-dialogue-ocr/assets/audio-edges/silero_vad_v6.onnx` 来自 faster-whisper 1.2.1 发行包；Silero VAD 与 faster-whisper 的 MIT 许可随模型放在同目录。
- 可选安装入口使用 [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) 1.2.1（MIT）、PyAV 及其依赖。完整许可应以安装包为准。
- 转写模型 [Systran/faster-whisper-small](https://huggingface.co/Systran/faster-whisper-small) 固定到提交 `536b0662742c02347bc0e980a01041f333bce120`，由安装脚本下载，发布附件不包含其权重。上游 [OpenAI Whisper](https://github.com/openai/whisper) 使用 MIT 许可。
- 用户上传的 `tools/voice-library` 参考代码保持原样；其来源说明见该目录 README。
