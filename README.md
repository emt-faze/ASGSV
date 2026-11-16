# ASGSV · 鸣潮与异环剧情配音工具

ASGSV 是一款 Windows 桌面工具，为游戏里没有原声的中文对白配音。在《鸣潮》中，工具读取游戏日志定位当前台词，翻页时立即停掉上一句。如果新录音完成时玩家已经翻页，音频会保存下来供下次使用。《异环》优先读取日志编号，缺少编号时用字幕定位游戏原文，也可以自动查找安装目录、提取台词并建立配音项目。

**[下载异环版 v0.4.14-nte.4（含字幕模型和资源读取组件）](https://github.com/emt-faze/ASGSV/releases/download/dialogue-generator-v0.4.14-nte.4/dialogue-generator-v0.4.14-nte.4-windows-with-ocr-models.zip)** · [异环版发布说明](https://github.com/emt-faze/ASGSV/releases/tag/dialogue-generator-v0.4.14-nte.4)

**[下载 Windows 版 v0.4.14（含两套鸣潮任务配音）](https://github.com/emt-faze/ASGSV/releases/download/wuwa-v0.4.14/ASGSV-v0.4.14-Windows-Quests.zip)** · [发布说明及 SHA-256 校验文件](https://github.com/emt-faze/ASGSV/releases/tag/wuwa-v0.4.14)

## 在鸣潮中试听

1. 准备 Windows 10/11 和 64 位 Python 3.12，将上面的 ZIP 完整解压到可写目录。
2. 首次联网运行 **安装鸣潮日志配音.bat**，完成后双击 **ASGSV.exe**。在「设置」里选好鸣潮安装目录，保存设置。
3. 进入下表中的任务对白。需要重新连接时，点击「重新连接 / 刷新音频」。**F8** 暂停，**F9** 从头重播当前句，**F10** 跳过。

| 随 v0.4.14 发布的任务 | 中文台词 | 声线 |
| --- | ---: | --- |
| 它记得 | 13 句 | 盈叶 B 版 |
| 失落的味道 | 168 句 | 鲙娘使用「元气茶香」，陶老使用「守艺老匠」，漂泊者为男声 |

播放这 181 句可以直接使用包内录音。任务包已核对《鸣潮》3.7.19 和 3.8.9 中文资源，工具按游戏资源清单匹配。其他普通支线可在任务库建立台词索引后自行配音，按角色自称、称谓和相邻对白选择声线。使用 BreezeBlue 合成时，在「设置」中填写自己的密钥和每日预算。已有同声线录音可直接复用。

## 异环与本地配音

准备 Windows 10/11 和 64 位 Python 3.12，下载异环版完整 ZIP 并解压。首次联网运行 **安装异环配音.bat**，随后双击 **打开异环配音.bat**。在“任务库与配音准备”页自动识别安装目录、提取中文台词，按角色或关键词检索并建立配音项目。实时配音前通过“字幕校准”选择字幕区域，打开游戏后连接。

“实时配音”和“任务库与配音准备”共用台词库。游戏更新后再次进入准备页会检查资源版本。使用系统中文声音即可开始配音，角色声线可在项目中另行设置。[异环版安装与使用](docs/异环配音v0.4.14-nte.4.md) · [台词生成器使用指南](docs/台词生成工具使用指南.md) · [本地角色模型安装](docs/模型安装.md)

## 仓库文件

| 位置 | 内容 |
| --- | --- |
| [nte-dialogue-ocr/](nte-dialogue-ocr/) | 桌面主程序、鸣潮日志识别与任务库、异环日志与字幕配音、台词整理及测试 |
| [wuwa-dubber/](wuwa-dubber/) | 两个游戏共用的语音合成、缓存和播放组件 |
| [local-voice-model/](local-voice-model/) | 可选的本地合成服务 |
| [scripts/](scripts/) 和 [launcher-source/](launcher-source/) | 安装与打包脚本、Windows 启动器源码 |
| [tools/](tools/) | 声线库和音频处理工具 |
| [docs/](docs/) | 使用、模型安装与开发文档 |
| [GitHub Releases](https://github.com/emt-faze/ASGSV/releases) | 各版本的下载附件。当前任务包的台词清单和录音在 Windows ZIP 的 `game-data/wuwa/voice-pack/` 中 |

从源码运行时，先执行根目录的安装脚本，再用 **打开台词生成工具.bat** 启动。只想在游戏里听已配好的台词，请下载上方含任务配音的 Windows ZIP。

本项目是玩家制作的非官方工具。随包语音为 AI 配音，游戏、角色和剧情归各自权利人所有。[第三方组件说明](THIRD_PARTY_NOTICES.md)
