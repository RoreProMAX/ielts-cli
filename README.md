# IELTS CLI

一个通用、轻量的终端英语练习工具：在普通终端、Agent Desktop 的 CLI 窗口，或开发与学习的空隙里随手练几分钟，不需要打开网页或接入另一套服务。它是独立的 Python 实现，受 Qwerty 打字学词思路启发，不是上游项目的官方 fork。

默认源码/稳定包运行 `apps/v3.3.1`，仓库同时保留 `apps/v1.0.0`、`apps/v2.0.0`、`apps/v3.2.0`、`apps/v3.3.0` 和 Beta `apps/v3.3.1-beta.1`。稳定版下载使用 [stable Release](https://github.com/RoreProMAX/ielts-cli/releases/latest)；Beta 保留独立的 [v3.3.1-beta.1 Release](https://github.com/RoreProMAX/ielts-cli/releases/tag/v3.3.1-beta.1) 作为历史版本。五本词库合计 19,086 个有效词条，按每章 20 词组织。

## 项目声明

本项目是我在 vibe coding 过程中，利用等待 Agent 输出的闲暇时间做的小作品，目前仍处于半成品阶段，功能、体验和兼容性都还有待完善。欢迎各位使用者提出问题、建议和指正。

感谢 Qwerty 以及所有上游项目的开源，也感谢相关作者和维护者分享代码、词库与工具。上游来源和许可证说明见 [第三方声明](THIRD_PARTY_NOTICES.md)。

## 快速开始

下载 [Release ZIP](https://github.com/RoreProMAX/ielts-cli/releases/latest)，或克隆仓库：

```sh
git clone https://github.com/RoreProMAX/ielts-cli.git
cd ielts-cli
```

需要 Python 3.10+。V3.3.1 在 Windows 使用标准库 VT 输出与原生 console 输入后端，主版本不再依赖 PDCurses 绘制；`windows-curses==2.4.2` 仅用于旧版本和对照诊断；需要运行这些版本时再安装：

```powershell
py -3 -m pip install -r requirements-windows.txt
```

启动：Windows 双击 `start.bat`，macOS 双击 `start.command`，Linux 运行 `sh start.sh`。默认选择 V3.3.1 稳定版，也可直接执行：

```sh
python3 launcher.py --app-version 3
```

在 Agent Desktop 底部终端中运行即可；终端至少需要 40 列、6 行。宿主可能拦截 F10，此时用 Esc 或空输入时的 `?` 打开菜单。

### 安装后直接输入 `ielts`

希望在任意目录输入 `ielts` 就能打开，可以把下面这段话交给负责安装的 Agent：

> 请按本仓库 `docs/FOR_AGENTS.md` 安装 IELTS CLI，并配置 `ielts` 命令。根据我的操作系统、实际使用的 shell、Python 和安装目录选择合适的命令入口，将入口目录加入用户 PATH，让重新打开的终端也能使用。入口要指向我安装的版本，保留启动参数与原有数据设置；已有同名命令时先说明指向和处理方案，不直接覆盖。最后从安装目录之外验证 `ielts --no-update --version` 和 `ielts --doctor`，告诉我以后输入 `ielts` 即可启动。

手动配置也遵循相同步骤：把完整程序放在固定目录；Linux/macOS 在用户命令目录（如 `~/.local/bin`）建立 `ielts` 包装入口，Windows 在用户命令目录建立 `ielts.cmd`；入口调用安装目录中的 `launcher.py`，再把**入口所在目录**加入用户 PATH。平台处理和验收细节见 [forAgent 的命令配置说明](docs/FOR_AGENTS.md#安装后配置-ielts-命令)。

配置后重新打开终端；嵌入式终端仍找不到命令时，重新打开其宿主应用。之后可运行：

```sh
ielts --no-update --version
ielts --doctor
ielts
```

当前 ZIP 和启动脚本不会自动注册命令；以上是用户或安装 Agent 可完成的配置步骤。命令使用所安装的版本，稳定版和 Beta 的选择仍按前述下载说明。移动或删除程序目录后，需要同步调整入口。

## 主要功能

- 跟打 `copy`、英选中 `en_to_zh`、中选英 `zh_to_en`、默写 `recall`，V3 默认混合学习并分别记录题型成绩。
- 中选英和默写显示词库已有的完整中文释义；长释义可以翻页。
- F7/Ctrl+D 选词库，F8/Ctrl+K 选章节，F9/Ctrl+B 进入或暂停到期复习，F11/Ctrl+Y 查看今日任务。
- 每日任务完成后默认进入增量例句与搭配练习；当前材料覆盖 124 个词。学习过程中加入例句默认关闭，可在 Esc 菜单的学习计划中开启；从增量例句返回菜单后可继续单词学习。
- V3.3.1 稳定版 启动时默认后台检查公开 stable Release；更新通道默认为 stable，Esc 菜单“版本与更新”可选择 beta。Beta 通道只接受高于当前版本的 stable/beta，不降级；只在用户确认后下载并安装，更新不抢题、不自动重启。稳定用户不会收到 Beta。
- V3.3.0 可通过原 stable channel 更新到 V3.3.1，旧 Beta 也可正常升级到同一 stable。V3.2.0 及更早版本没有 updater，需手动安装 V3.3.1 一次；不要求先安装 Beta 或其它中间版本。`--no-update` 仅本次使用原入口版本并跳过检查，不修改开关。
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

V3.3.1 稳定版 继承已验证的 Windows VT/native console 修复；本地 174 项测试通过，发布提交的四平台 CI 和终端回放见[V3.3.1 发布页](https://github.com/RoreProMAX/ielts-cli/releases/tag/v3.3.1)。V3.3.0 的 Windows PDCurses 中文重绘失败仍保留在 [STATUS.md](docs/STATUS.md) 作为历史基线。字体、DPI、IME 和实际 Desktop 宿主仍需人工验收。

自有代码使用 GPL-3.0，见 [LICENSE](LICENSE)。词库来源的独立 MIT notice、固定 commit、原始 URL 和 SHA-256 见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 及各版本 `data/source.json`。

给编程 Agent 的操作文档：[forAgent 部署与优化指南](docs/FOR_AGENTS.md)。它用于部署和维护，不限定产品的使用人群。
