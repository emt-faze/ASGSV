# ASGSV · 鸣潮与异环剧情配音工具

ASGSV 是一款 Windows 桌面工具，为游戏里没有原声的中文对白配音。在《鸣潮》中，工具读取游戏日志定位当前台词，翻页时立即停掉上一句。如果新录音完成时玩家已经翻页，音频会保存下来供下次使用。《异环》使用窗口采集和 OCR 识别字幕，也可以先导入台词，核对角色、生成录音后再进游戏播放。

**[下载鸣潮 v0.4.15 Windows 便携版](https://github.com/emt-faze/ASGSV/releases/tag/wuwa-v0.4.15)**

1. 在 Windows 10/11（x64）上，将 ZIP 完整解压到可写目录，再双击 **ASGSV.exe**。
2. 在工作台选择「鸣潮」，按提示确认安装目录，然后点击「一键配置并开始」。
3. 在游戏中进入《它记得》（13 句）或《失落的味道》（168 句）试听。**F8** 暂停，**F9** 重播，**F10** 跳过。

便携版附带 Python、Qt、OCR 模型和 181 句预配音。播放这些录音无需合成账号，但任务语音包会核对游戏资源指纹。要生成新台词，可在「设置」中填入自己的 BreezeBlue 密钥。若使用本地角色模型，请按 [模型安装说明](docs/模型安装.md) 准备。

鸣潮工作台支持后台读取日志、换句时停播、点击说话人换音、分栏任务库、背景设置、灵动悬浮窗和专名纠音。异环专版的台词提取功能见 [异环 v0.4.14-nte.4 发布页](https://github.com/emt-faze/ASGSV/releases/tag/dialogue-generator-v0.4.14-nte.4)。

## 异环与本地配音

运行 **安装台词生成工具.bat**，安装后双击 **打开台词生成工具.bat**。可从游戏窗口识别字幕，也可以导入识别记录或台词文本，逐行编辑和试听。[台词生成器使用指南](docs/台词生成工具使用指南.md) · [本地角色模型安装](docs/模型安装.md)

## 仓库文件

| 位置 | 内容 |
| --- | --- |
| [nte-dialogue-ocr/](nte-dialogue-ocr/) | 桌面主程序、鸣潮日志识别与任务库、异环 OCR、台词编辑及测试 |
| [wuwa-dubber/](wuwa-dubber/) | 两个游戏共用的语音合成、缓存和播放组件 |
| [local-voice-model/](local-voice-model/) | 可选的本地合成服务 |
| [scripts/](scripts/) 和 [launcher-source/](launcher-source/) | 安装与打包脚本、Windows 启动器源码 |
| [tools/](tools/) | 声线库和音频处理工具 |
| [docs/](docs/) | 使用、模型安装与开发文档 |
| [GitHub Releases](https://github.com/emt-faze/ASGSV/releases) | 各版本的下载附件。当前任务包的台词清单和录音在 Windows ZIP 的 `game-data/wuwa/voice-pack/` 中 |

从源码运行时，先执行根目录的安装脚本，再用 **打开台词生成工具.bat** 启动。只想在游戏里听已配好的台词，请下载上方含任务配音的 Windows ZIP。

本项目是玩家制作的非官方工具。随包语音为 AI 配音，游戏、角色和剧情归各自权利人所有。[第三方组件说明](THIRD_PARTY_NOTICES.md)
