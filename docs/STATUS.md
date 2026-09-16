# 开发状态

## V3.3 当前状态

- 已修复菜单 9 增量例句返回后菜单 2 无法回到单词学习的问题；恢复时保留当前题目、输入和评分上下文。
- 已加入启动时后台 stable Release 检查、用户可关闭设置、确认后下载/安装和保存并重启或下次启动使用的流程。更新器不自动安装、重启或打断学习。
- V3.3.1 稳定版：本地 174 项测试通过；发布提交的四平台真实终端结果与安装包检查见[V3.3.1 发布页](https://github.com/RoreProMAX/ielts-cli/releases/tag/v3.3.1)。Beta 的 174 项本地测试及 4 平台 10 步结果是历史依据，见 [Actions 35059466717](https://github.com/RoreProMAX/ielts-cli/actions/runs/35059466717)。

## 已验证

- V3.2：Linux 本机 120 tests 通过（历史记录）。
- 终端最小尺寸 40 列、6 行的拒绝路径和 6 行交互布局已验证。
- 从不同工作目录启动的重定位行为已验证。
- 离线容器验证已完成；文字练习不依赖发音网络。
- Windows 旧版本适配代码与 `windows-curses==2.4.2` 依赖保留；V3.3.1 稳定版 继承标准库 VT/native console 后端，Beta 历史真实终端 10 步 CI 已通过，稳定版发布提交的验证记录见[V3.3.1 发布页](https://github.com/RoreProMAX/ielts-cli/releases/tag/v3.3.1)。

## 待验证

- 字体、DPI、IME、物理键盘和实际 Desktop 宿主交互仍需人工验收；CI 结果不等于所有 Windows/macOS 宿主兼容。
- CI 已配置 Linux 测试和三平台启动检查，当前结果以仓库 Actions 为准。
- 各类桌面宿主的按键、焦点和缩放行为仍需实测；菜单使用 Esc/`?`，不依赖 F10。

真实 PTY/ConPTY 交互与可下载回放由单独的 CI 任务执行，覆盖 Linux、macOS ARM/Intel 和 Windows；对应运行结果及范围见 [终端兼容测试](TERMINAL_TESTING.md)。它与实体终端宿主的人工验收分别记录。

## 2026-09-16 真实终端回归结果

- Linux、macOS 15 ARM64 与 Intel：固定的 10 步交互场景通过，含例句往返、更新设置、F9/F11、缩放、保存和重启恢复。
- V3.3.0 Windows Server 2025 / ConPTY 的 PDCurses 中文局部重绘失败是历史基线：将“中文”计为 2 列，双列 CJK 模型应为 4 列。旧证据见 [Actions 35055258617](https://github.com/RoreProMAX/ielts-cli/actions/runs/35055258617)。
- V3.3.1 稳定版 继续使用 `windows_terminal.py`；Beta 历史证据中 Windows “中文”在 VT 预期、内部网格和原生控制台均为 4 列，两种 codepage 对照及 10 步场景通过，见 [Actions 35059466717](https://github.com/RoreProMAX/ielts-cli/actions/runs/35059466717)。稳定版发布记录见[V3.3.1 发布页](https://github.com/RoreProMAX/ielts-cli/releases/tag/v3.3.1)。

## 已知限制与缺陷

- 例句与搭配只有 124 词，不能覆盖全部字库；缺少材料时相关题型会跳过或回退。
- 原始词条和中文释义未经逐词重校，可能存在来源内容不完美的问题。
- 判分采用精确字符串规则；同义词、替代表达和语法上可接受的变体不会自动判正确。
- 复习间隔是项目内的简化算法，不是 FSRS 实现，也不应宣传为 FSRS。
- 版本进度迁移是一次性复制；运行后不同版本的新增记录不会双向合并。
- 退出后的后台提醒依赖 Linux `systemd --user`；其他平台只有程序内提醒。
- 更新按 channel 接受 `RoreProMAX/ielts-cli` 的公开 Release，并校验官方 asset 的 SHA-256 与 ZIP 清单；stable 不接受 Beta，beta 接受高于当前版本的 stable/beta，均不接受 draft 或降级。程序与内置词库随完整发布包更新，不自动升级 Python、curses 或 ffplay。压缩包保存在当前进度目录的 `updates/downloads/`，解压程序保存在 `updates/releases/`，启用前保留 JSON/SQLite 备份。
- 更新 channel 默认是 `stable`，可选 `beta`；beta 接受高于当前版本的 stable/beta，不降级。stable 用户不会收到 Beta。V3.3.0 可通过 stable channel 更新到 V3.3.1；V3.2.0 及更早版本没有 updater，需手动安装 V3.3.1 一次；Beta 与 stable 数据按版本隔离，不双向同步。
- 原 PDCurses 中文重绘失败是历史基线，保留其失败证据；V3.3.1 稳定版 继承 Beta 已验证的 VT/native console 修复，稳定版发布记录见[V3.3.1 发布页](https://github.com/RoreProMAX/ielts-cli/releases/tag/v3.3.1)，人工宿主体验单独记录。
- 需要 Python 3.10+；Linux/macOS 使用 curses，Windows V3.3.1 使用标准库 VT/native console，旧版才需要 `windows-curses`。可选发音另需 `ffplay`。
- 终端宿主若吞掉功能键、改变 TERM 或不能提供 40×6 的最小尺寸，交互体验会受影响。

## 按模块查看

| 模块 | 当前可用 | 已知限制 / 优化入口 |
|---|---|---|
| `library.py`、`data/` | 五本词库、独立章节与进度、SHA/数量校验 | 仅接受 `language: en`；单词库文件 5 MiB；新词库仍需维护 catalog，原始释义质量不一致 |
| `quiz.py`、`content.json` | 双向选择、默写、听写、例句、搭配、完整释义 | 124 词静态配套材料；不会自动生成或抓取例句；判分不理解同义词和语义等价 |
| `practice.py` | 混合阶段、错题补练、暂停与幂等恢复 | 使用固定阶段和重试规则；不是自由对话或自适应教学模型 |
| `scheduler.py` | 分上下文记录成绩、到期排程、增量隔离 | 简化间隔而非 FSRS；目标计数表示练习量，不能等同于掌握率 |
| `ielts.py` | curses UI、菜单、翻页、最小终端布局 | 宿主焦点和按键截获仍影响体验；没有原生 GUI 或已承诺的 IPC 服务 |
| `pronunciation.py` | 单词发音、取消待播、本地缓存 | 外部 ffplay + 首次联网；没有批量离线音频、句子朗读或语音识别接口 |
| `routine.py`、`reminders.py` | 每日目标、增量开关、程序内提醒 | 退出后的提醒只实现 Linux systemd 用户定时器 |
| `updater.py` | 后台稳定版检查、确认后下载、完整性校验、备份与切换 | 依赖 GitHub 可达；无下载百分比，不自动清理旧包；外部运行依赖需自行安装 |
| `versioning.py`、`launcher.py` | 版本隔离、复制迁移、便携数据目录 | 不合并不同版本的后续进度；默认第三版由根 `VERSION` 指定，发布时仍需同步应用版本和 CI |

锁互斥测试会故意令一个子进程失败，日志可能包含预期的异常堆栈；应结合 unittest 汇总和进程退出码判断结果。测试输出减噪仍可优化。
