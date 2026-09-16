# 真实终端兼容测试与回放

这套测试启动实际的 IELTS CLI 子进程，通过操作系统的终端会话发送按键并读取输出，不调用假的 `draw()`、`handle()` 或 `curses.wrapper()`。

## 运行环境

| CI 目标 | 终端后端 | 覆盖范围 |
|---|---|---|
| Ubuntu 24.04 x64 | POSIX PTY / pexpect | 同一套真实键盘输入与存档回归 |
| macOS 15 ARM64 | POSIX PTY / pexpect | Apple Silicon 的 Python/curses 交互 |
| macOS 15 Intel | POSIX PTY / pexpect | Intel 的 Python/curses 交互 |
| Windows Server 2025 x64 | ConPTY / native console backend | Windows 原生 Python 与 V3.3.1-beta.1 `windows_terminal.py` 交互；旧版 windows-curses 仅作对照 |

具体系统版本、CPU 架构、Python 和测试依赖版本写入每次 `report.json`。`passed: true` 才代表对应环境本次脚本通过；Windows Server 结果不等于所有 Windows 11 终端宿主已经人工验收。

## 固定操作

测试使用临时目录内的程序副本和预置学习数据，程序路径及进度路径均含中文和空格。顺序验证：

1. 启动实际 CLI，完成一个固定单词，给下一题留下部分输入。
2. 进入增量例句并输入部分答案，再返回单词学习；核对两边题目、输入和评分事件。
3. 用菜单第 10 项进入版本与更新，核对 stable 默认 channel、切换 beta 后再恢复 stable，关闭自动检查并返回。
4. 通过终端输入序列操作 F11 与 F9，核对面板、到期复习和返回学习。
5. 依次调整到 35×5、40×6、80×24；验证最小尺寸提示、重绘和更新菜单。
6. Ctrl+Q 保存退出，在 40×6 下重新启动，核对单词和例句输入，再退出。
7. 核对 SQLite 评分事件没有因导航而增加，也没有触发下载或更新激活。

网络更新检查、发音和系统提醒均关闭。子进程只继承必要的系统环境变量，不继承 API token；用户目录和数据路径全部指向临时位置。报告不包含个人学习数据库或真实用户记录。

## 本地执行

在隔离的 Python 开发环境中执行；Windows 将 `python3` 换成 `py -3`：

```sh
python3 -m pip install -r requirements-terminal-test.txt
python3 -B -m unittest discover -s tools -p 'test_terminal_*.py' -q
python3 -B tools/terminal_smoke.py --output artifacts/terminal-local
```

输出目录必须为空或尚不存在，已有证据不会被覆盖。测试依赖只用于验证，不是正常运行 CLI 的新增依赖。

## 查看 CI 结果

在仓库 Actions 中打开对应运行，下载 `terminal-linux-x64`、`terminal-macos-arm64`、`terminal-macos-intel` 或 `terminal-windows-x64` artifact。测试失败时也会保留已经采集的证据，CI 产物保留 90 天。

每个 artifact 包含：

- `replay.html`：可离线打开的关键步骤回放，支持逐帧和自动播放。
- `report.json`：平台、依赖版本、每一步结果、失败原因及退出码。
- `frames.json`、`screens.txt`：从实际终端输出重建的字符网格。
- `terminal-events.jsonl`：按键、原始终端输出、尺寸变化、标准终端查询回复及脱敏异常信息。

回放不是原生窗口截图。使用 pyte 解码 VT 输出；遇到宽字符被单字节字符覆盖留下的孤立延续格时，将该空格渲染为空白，原始字符流仍保留。测试另有回归覆盖这一解析边界。

Windows 旧版另有 `terminal-width-windows-x64` 诊断产物，记录 `curses.getyx()` 与活动 `CONOUT$` 控制台缓冲区，比较默认与 UTF-8 模式下固定字符串的列位置；V3.3.0 PDCurses 失败证据保留。V3.3.1 稳定版 主路径使用 `windows_terminal.py`；Beta 历史证据已验证 Windows VT 预期/内部/原生控制台双列 CJK，4 平台各 10 步场景通过，见 [Actions 35059466717](https://github.com/RoreProMAX/ielts-cli/actions/runs/35059466717)。稳定版的发布提交验证记录见[V3.3.1 发布页](https://github.com/RoreProMAX/ielts-cli/releases/tag/v3.3.1)；该结果不替代字体、DPI、IME 和实际 Desktop 宿主人工验收。

字体、DPI、输入法、物理键盘与宿主快捷键映射、真实扬声器输出仍需人工体验。该报告证明的是记录中的操作系统、终端后端、尺寸与固定操作路径。
