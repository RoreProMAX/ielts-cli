# IELTS CLI

一个通用、轻量的终端英语练习工具：在普通终端、Agent Desktop 的 CLI 窗口，或开发与学习的空隙里随手练几分钟，不需要打开网页或接入另一套服务。它是独立的 Python 实现，受 Qwerty 打字学词思路启发，不是上游项目的官方 fork。

默认运行 `apps/v3.2.0`，仓库同时保留 `apps/v1.0.0`、`apps/v2.0.0`。五本词库合计 19,086 个有效词条，按每章 20 词组织。

## 项目声明

本项目是我在 vibe coding 过程中，利用等待 Agent 输出的闲暇时间做的小作品，目前仍处于半成品阶段，功能、体验和兼容性都还有待完善。欢迎各位使用者提出问题、建议和指正。

感谢 Qwerty 以及所有上游项目的开源，也感谢相关作者和维护者分享代码、词库与工具。上游来源和许可证说明见 [第三方声明](THIRD_PARTY_NOTICES.md)。

## 快速开始

下载 [Release ZIP](https://github.com/RoreProMAX/ielts-cli/releases/latest)，或克隆仓库：

```sh
git clone https://github.com/RoreProMAX/ielts-cli.git
cd ielts-cli
```

需要 Python 3.10+。Windows 安装 `windows-curses==2.4.2`：

```powershell
py -3 -m pip install -r requirements-windows.txt
```

启动：Windows 双击 `start.bat`，macOS 双击 `start.command`，Linux 运行 `sh start.sh`。默认选择 V3.2，也可直接执行：

```sh
python3 launcher.py --app-version 3
```

在 Agent Desktop 底部终端中运行即可；终端至少需要 40 列、6 行。宿主可能拦截 F10，此时用 Esc 或空输入时的 `?` 打开菜单。

## 主要功能

- 跟打 `copy`、英选中 `en_to_zh`、中选英 `zh_to_en`、默写 `recall`，V3 默认混合学习并分别记录题型成绩。
- 中选英和默写显示词库已有的完整中文释义；长释义可以翻页。
- F7/Ctrl+D 选词库，F8/Ctrl+K 选章节，F9/Ctrl+B 进入或暂停到期复习，F11/Ctrl+Y 查看今日任务。
- 每日任务完成后默认进入增量例句与搭配练习；当前材料覆盖 124 个词。学习过程中加入例句默认关闭，可在 Esc 菜单的学习计划中开启。
- 增量练习不抬高正常新词/复习目标；到期量不足时按实际数量处理。
- 文字练习可离线。发音依赖 PATH 中的 `ffplay`，首次取某词音频需联网，之后使用本地缓存。

## 检查与数据

`--doctor` 适用于源码目录，会检查 Python、curses、SQLite、应用文件并显示解析后的数据路径：

```sh
python3 launcher.py --doctor
```

`--verify` 只适用于带发布清单的 Release 包。源码仓库不含 `BUNDLE_MANIFEST.json`，不要把源码目录的 `--verify` 结果当成发布包校验：

```sh
python3 launcher.py --verify
```

`--portable` 把进度和音频缓存写入当前目录的 `user-data/`，便于和代码目录一起移动。提交前不要包含个人进度、缓存、日志、账号或 API key。

## 文档

- [功能说明](docs/FEATURES.md) · [接口参考](docs/INTERFACES.md)
- [词库维护](docs/DICTIONARIES.md) · [开发状态](docs/STATUS.md)
- [部署指南](docs/部署指南.md) · [使用手册](docs/使用手册.md)
- [贡献指南](CONTRIBUTING.md) · [第三方声明](THIRD_PARTY_NOTICES.md)

## 当前状态与许可证

Linux 本机已完成 V3 的 120 项测试、6 行终端/重定位检查和离线容器验证。Windows/macOS 已有适配代码，但真实 Desktop 交互尚未验收，自动化检查结果以仓库 Actions 为准。已知限制和缺陷见 [STATUS.md](docs/STATUS.md)。

自有代码使用 GPL-3.0，见 [LICENSE](LICENSE)。词库来源的独立 MIT notice、固定 commit、原始 URL 和 SHA-256 见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 及各版本 `data/source.json`。

给编程 Agent 的操作文档：[forAgent 部署与优化指南](docs/FOR_AGENTS.md)。它用于部署和维护，不限定产品的使用人群。
