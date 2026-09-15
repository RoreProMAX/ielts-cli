# IELTS CLI

一个独立实现的终端英语词汇练习工具，受 Qwerty 打字学词思路启发，适合 IELTS、CET 和通用词汇复习。本仓库默认运行 `apps/v3.2.0`，同时保留 `apps/v1.0.0` 与 `apps/v2.0.0` 供对照和使用。

## 功能

- 5 本词库，共 19,086 个有效词条；按每章 20 词组织，最后一章按实际数量显示。
- 每日学习自动安排跟打（`copy`）、英选中（`en_to_zh`）、中选英（`zh_to_en`）和默写（`recall`）。
- 中选英和默写显示词库中的完整中文释义；长释义可用方向键或 PageUp/PageDown 翻页。
- V3 默认以混合题型分组学习，分别记录各题型成绩，并支持到期复习。
- 124 个词有增量例句与搭配练习。每日任务完成后默认进入增量练习；到期量不足时按实际数量处理。
- 增量练习不会抬高正常新词或复习目标，也不会因答对而连续推远原复习安排。
- 学习中的例句穿插默认关闭，可从 Esc 菜单的学习计划中开启。
- 文字练习支持离线使用。发音使用 `ffplay`；某个词首次取音频时需要联网，成功后使用本地缓存。

## 快速开始

可先下载 ZIP，或在仓库页面复制实际地址后运行：

```sh
git clone https://github.com/RoreProMAX/ielts-cli.git
cd ielts-cli
```

需要 Python 3.10 或更新版本。Windows 还需要安装与当前 Python 对应的 `windows-curses==2.4.2`：

```powershell
py -3 -m pip install -r requirements-windows.txt
```

启动方式：

```text
Windows:     start.bat
macOS:       双击 start.command，或 sh start.command
Linux:       sh start.sh
```

启动脚本会调用 `launcher.py`，默认选择 V3.2。也可以直接运行：

```sh
python3 launcher.py --app-version 3
```

切换旧版本使用 `--app-version 1` 或 `--app-version 2`。三个版本的接口保持兼容；版本 3 是默认推荐版本。

## 检查环境

`--doctor` 可检查 Python、curses、SQLite、应用文件和数据路径，适用于源码仓库：

```sh
python3 launcher.py --doctor
```

`--verify` 用于按 SHA-256 核对带有发布清单的分享包。源码仓库默认不包含 `BUNDLE_MANIFEST.json`，因此应在发布包中使用：

```sh
python3 launcher.py --verify
```

可组合使用两个选项。Windows 将 `python3` 替换为 `py -3`，或使用 `start.bat --doctor`。

## 数据与便携模式

默认进度和设置写入当前用户的数据目录。使用 `--portable` 后，进度和音频缓存写入仓库目录下的 `user-data/`，便于连同整个目录移动：

```sh
python3 launcher.py --app-version 3 --portable
```

程序不会隐式导入接收者电脑上的其他学习记录。分享或提交前请退出程序，不要把个人 `user-data/`、缓存、日志、账号或 API key 提交到 Git。

## 例句与菜单

每日基础任务完成后，V3 默认进入增量例句与搭配练习；到期词少于配额时按实际到期量计算。Esc 菜单第 9 项可手动开始增量练习；学习计划中的第 7 项控制“每日任务完成后增量练习”，第 8 项控制“学习过程中加入例句”。

## 文档

- [部署指南](docs/部署指南.md)
- [使用手册](docs/使用手册.md)
- [贡献指南](CONTRIBUTING.md)
- [第三方声明](THIRD_PARTY_NOTICES.md)

## 平台状态

Linux 已完成 120 项测试、重定位检查和离线容器验证。代码包含 Windows 适配；Windows 与 macOS 的交互行为尚未进行真机验收，跨平台 smoke CI 已配置，首次远端运行结果将在 Actions 中显示。

## 来源与许可证

这是独立的 Python 实现，不是上游项目的官方 fork，也不代表上游认可。五本字库来自 `qwerty-learner-vscode` 的固定提交；每个版本的 `data/source.json` 保存原始 URL、SHA-256 和来源元数据，`data/QWERTY-VSCODE-LICENSE.txt` 保存对应 MIT notice。主项目 `qwerty-learner` 当前为 GPL-3.0，与本项目字库来源的许可证声明不同；详见 [第三方声明](THIRD_PARTY_NOTICES.md)。

独立应用代码采用 [GPL-3.0-only](LICENSE)。

项目地址：[RoreProMAX/ielts-cli](https://github.com/RoreProMAX/ielts-cli)。应用代码使用 GPL-3.0-only；第三方内容保留各自的许可证及来源声明。
