# ASGSV · 鸣潮支线配音工作台

ASGSV 是一款 Windows 桌面工具，为《鸣潮》没有原声的中文对白补上配音。它通过游戏日志定位当前台词。翻页时会停止上一句，迟到的合成结果会保存下来供下次使用。

**[下载鸣潮 v0.4.18 Windows x64 便携版](https://github.com/emt-faze/ASGSV/releases/download/wuwa-v0.4.18/ASGSV-v0.4.18-Windows-x64-Portable.zip)** · [发布说明及 SHA-256 校验](https://github.com/emt-faze/ASGSV/releases/tag/wuwa-v0.4.18)

## 解压即用

1. 在 Windows 10/11 x64 上，将 ZIP 完整解压到可写目录，再双击 **ASGSV.exe**。便携包内置独立的 64 位 Python 3.12，无需在系统安装 Python，也不会使用已有的系统 Python。
2. 打开鸣潮工作台，在 **设置 → 游戏与角色 → 本机资源探测** 自动定位游戏安装目录。可以在实时配音页启动鸣潮，再点 **一键配置并开始** 连接游戏日志。
3. 进入下表中的任务对白。**F8** 暂停，**F9** 重播当前句，**F10** 跳过。

| 随包任务 | 配音句数 |
| --- | ---: |
| 它记得 | 13 |
| 失落的味道 | 168 |

这 181 句预配音可在匹配的中文游戏资源版本上播放，无需配置合成账号。要补配其他任务，在左侧打开 **任务库**，选中任务后点 **一键生成本任务配音**。使用 BreezeBlue 时，在 **任务库 → 每日配音计划 → 配音与预算** 填入自己的密钥和每日预算。本地角色合成需要另行准备模型。[本地模型安装](docs/模型安装.md)

## 更新程序和配音资源

打开工作台右上角 **更新**。点 **一键在线升级** 会检查 GitHub 发布版本、下载程序更新并在空闲时重启。新版主窗口启动成功后才确认升级。每日配音进行时请等待结束再升级。

- **更新台词索引或单个任务**：点 **检查资源**，选中中文台词索引或任务名，再点 **更新选中资源**。任务库自动刷新，日志监听在下一句读取新配音。任务包仍须匹配本机游戏资源和角色声线。
- **使用网盘**：下载发布方提供的程序更新包 `ASGSV-v0.4.18-Update.zip`、索引包或任务包，保持 ZIP 原样，点 **导入更新包…**。程序与资源共用签名校验。初次安装选文件名含 `Portable` 的完整便携包。
- **从 v0.4.17 首次升级**：关闭旧工作台，把 [首次更新助手](https://github.com/emt-faze/ASGSV/releases/download/wuwa-v0.4.18/ASGSV-Update-Assistant.zip) 中的 `ASGSV-Update` 文件夹解压到旧版 `ASGSV.exe` 旁边，双击其中的 `Start-Update.bat`。可以在线升级，也可以导入网盘取得的程序更新包。保留原安装目录即可沿用个人录音、声线、设置和每日计划。

## v0.4.18 功能

- **实时配音** 根据游戏日志换句，显示当前任务、对白、播放进度和最近记录。点击历史对白可重听。
- **任务库** 可扫描游戏日志中已下发的进行中和可接取支线，按账号同步任务名称与进度。支持搜索音色、逐句试听和一键补齐任务录音。
- **每日配音计划** 可勾选任务或一键加入全部。Windows 在设定时间执行，错过时在下次登录后补做。可关闭工作台，每日计划与实时配音共用本机 BreezeBlue 账本，每日计划最多使用 400 积分。[配置与使用说明](nte-dialogue-ocr/docs/每日配音计划.md)
- **角色分析** 可用独立的模型 API Key 根据对白选择新角色的声线，默认预填 DeepSeek，支持在界面测试连接。
- **角色音色** 点击当前说话人或任务库的角色名可换声线。实时对白换声后会触发当前句重播，已有同声线音频可复用。
- **个性化** 支持自定义舞台背景、遮罩与虚化、自动收展的悬浮窗和专有名词发音词典。词典只影响要送去合成的文本，屏幕保留游戏原文。

另有独立的异环发行版和使用文档。[异环专版下载与说明](https://github.com/emt-faze/ASGSV/releases/tag/dialogue-generator-v0.4.14-nte.4)

## 仓库文件

| 路径 | 内容 |
| --- | --- |
| [nte-dialogue-ocr/](nte-dialogue-ocr/) | 桌面入口、游戏对白识别、任务库、台词编辑和回归测试 |
| [wuwa-dubber/](wuwa-dubber/) | 配音合成、缓存和播放组件 |
| [local-voice-model/](local-voice-model/) | 可选本地合成服务 |
| [scripts/](scripts/) 和 [launcher-source/](launcher-source/) | 打包、安装和 Windows 启动器 |
| [tools/](tools/) 和 [docs/](docs/) | 声线工具及使用说明 |

## 从源码安装与打包

从源码运行时，双击根目录的 **安装鸣潮日志配音.bat**，等安装完成后运行 **打开台词生成工具.bat**。安装器会自动准备官方 Python 3.12.8 和 pip，依赖优先尝试清华镜像，失败后使用官方 PyPI。v0.4.18 便携版已内置同一套独立运行环境和 pip，位于 `runtime\python\`。早期版本仍可从旧发布页下载。

开发者可先运行 `scripts\ensure_embedded_python.bat`，再从仓库根目录构建。需要提供已校验的 OCR 模型和用于公开发布的任务音频目录。

```powershell
.\runtime\python\python.exe -I -X utf8 .\scripts\build_wuwa_portable.py --output .\dist --models-dir PATH_TO_OCR_MODELS --voice-pack PATH_TO_VOICE_PACK
```

任务包的台词清单和录音位于 ZIP 内的 `game-data/wuwa/voice-pack/`。

本项目是玩家制作的非官方工具。随包语音为 AI 配音，游戏、角色和剧情归各自权利人所有。[第三方组件说明](THIRD_PARTY_NOTICES.md)
