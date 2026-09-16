# IELTS CLI

一个通用、轻量的终端英语练习工具：在普通终端、Agent Desktop 的 CLI 窗口，或开发与学习的空隙里随手练几分钟，不需要打开网页或接入另一套服务。它是独立的 Python 实现，受 Qwerty 打字学词思路启发，不是上游项目的官方 fork。

默认源码/Beta 包运行 `apps/v3.3.1-beta.1`，仓库同时保留 `apps/v1.0.0`、`apps/v2.0.0`、`apps/v3.2.0`、`apps/v3.3.0`。稳定版下载仍使用 [stable Release](https://github.com/RoreProMAX/ielts-cli/releases/latest)；Beta 使用独立的 [v3.3.1-beta.1 Release](https://github.com/RoreProMAX/ielts-cli/releases/tag/v3.3.1-beta.1)。五本词库合计 19,086 个有效词条，按每章 20 词组织。

## 项目声明

本项目是我在 vibe coding 过程中，利用等待 Agent 输出的闲暇时间做的小作品，目前仍处于半成品阶段，功能、体验和兼容性都还有待完善。欢迎各位使用者提出问题、建议和指正。

感谢 Qwerty 以及所有上游项目的开源，也感谢相关作者和维护者分享代码、词库与工具。上游来源和许可证说明见 [第三方声明](THIRD_PARTY_NOTICES.md)。

## 快速开始

下载 [Release ZIP](https://github.com/RoreProMAX/ielts-cli/releases/latest)，或克隆仓库：

```sh
git clone https://github.com/RoreProMAX/ielts-cli.git
cd ielts-cli
```

需要 Python 3.10+。V3.3.1-beta.1 在 Windows 使用标准库 VT 输出与原生 console 输入后端，主版本不再依赖 PDCurses 绘制；`windows-curses==2.4.2` 仍为旧版本和对照诊断依赖：

```powershell
py -3 -m pip install -r requirements-windows.txt
```

启动：Windows 双击 `start.bat`，macOS 双击 `start.command`，Linux 运行 `sh start.sh`。默认选择 V3.3，也可直接执行：

```sh
python3 launcher.py --app-version 3
```

在 Agent Desktop 底部终端中运行即可；终端至少需要 40 列、6 行。宿主可能拦截 F10，此时用 Esc 或空输入时的 `?` 打开菜单。

## 主要功能

- 跟打 `copy`、英选中 `en_to_zh`、中选英 `zh_to_en`、默写 `recall`，V3 默认混合学习并分别记录题型成绩。
- 中选英和默写显示词库已有的完整中文释义；长释义可以翻页。
- F7/Ctrl+D 选词库，F8/Ctrl+K 选章节，F9/Ctrl+B 进入或暂停到期复习，F11/Ctrl+Y 查看今日任务。
- 每日任务完成后默认进入增量例句与搭配练习；当前材料覆盖 124 个词。学习过程中加入例句默认关闭，可在 Esc 菜单的学习计划中开启；从增量例句返回菜单后可继续单词学习。
- V3.3.1-beta.1 启动时默认后台检查公开 stable Release；更新通道默认为 stable，Esc 菜单“版本与更新”可选择 beta。Beta 通道只接受高于当前版本的 stable/beta，不降级；只在用户确认后下载并安装，更新不抢题、不自动重启。稳定用户不会收到 Beta。
- V3.3.0 及更早版本没有 Beta 通道入口；首次体验需手动下载 V3.3.1-beta.1，开启 beta 通道后才会检查后续 Beta。无需先安装中间版本。`--no-update` 仅本次使用原入口版本并跳过检查，不修改开关。
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

V3.3.1-beta.1 的测试与真实终端验收以当前验收记录和仓库 Actions 为准，文档不预先宣称 Windows/macOS 通过。V3.3.0 的 Windows PDCurses 中文重绘失败保留在 [STATUS.md](docs/STATUS.md) 作为历史基线。已知限制和缺陷见 [STATUS.md](docs/STATUS.md)。

自有代码使用 GPL-3.0，见 [LICENSE](LICENSE)。词库来源的独立 MIT notice、固定 commit、原始 URL 和 SHA-256 见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 及各版本 `data/source.json`。

给编程 Agent 的操作文档：[forAgent 部署与优化指南](docs/FOR_AGENTS.md)。它用于部署和维护，不限定产品的使用人群。
