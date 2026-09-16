# forAgent：部署、维护与优化接口

本文供编程 Agent 阅读和执行。产品本身适用于普通终端、开发空隙、学习、远程终端及 Agent Desktop 等多种场景；运行不依赖模型、Agent SDK 或 API key。

## 1. 先确定当前状态

在仓库根目录执行：

```sh
git status --short
python3 launcher.py --doctor
python3 launcher.py --version
python3 launcher.py --list-dictionaries
```

Windows 使用 `py -3` 替换 `python3`。不要将用户未提交的改动覆盖为发布快照。功能与平台边界见 [STATUS.md](STATUS.md)，CLI 参数见 [INTERFACES.md](INTERFACES.md)。

`--doctor` 只检查依赖和显示路径，不创建学习进度。`--list-dictionaries` 列出目录信息，不等于逐文件完成摘要/内容校验。`--verify` 需要 Release ZIP 的清单；源码 checkout 使用 Git 和测试校验。

## 2. 部署路径

1. 准备 Python 3.10+。Windows 安装 `requirements-windows.txt`；Linux/macOS 使用 Python 的 curses。
2. 先运行 `--doctor`。发音是可选能力，需要 PATH 中存在 `ffplay`；文字练习可离线运行。
3. 用隔离目录验证启动与存储，再让用户启动真实学习界面：

```sh
python3 - <<'PY'
import json
import subprocess
import sys
import tempfile
with tempfile.TemporaryDirectory() as directory:
    result = subprocess.run(
        [sys.executable, 'launcher.py', '--data-dir', directory, '--stats'],
        capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)['attempts'] == 0
PY
```

4. 用户正常运行 `sh start.sh`、`start.command` 或 `start.bat`。需要携带进度时加 `--portable`；移动前退出程序。
5. 手工换包时先复制旧版本进度到新版本目录，保留源目录；V3.3 起使用程序内更新时，新代码沿用当前进度目录，切换前会备份。不要让旧版和新版同时写同一份数据。

后台提醒默认关闭。只有用户明确要求时才执行 `--reminders enable`；这会创建本程序的 Linux 用户定时器。它不是普通启动检查的一部分。V3.3.1-beta.1 的更新检查是程序运行时后台线程，默认 channel 为 stable，可选 beta，默认开启但可关闭；V3.2.0 及更早版本没有 Beta 入口，需先手动安装 Beta。更新线程只读取高于当前版本的公开 stable/beta Release，不自动下载、安装或重启，也不得抢占题目；`--no-update` 仅本次使用原入口版本并跳过检查。测试用 `IELTS_DISABLE_UPDATE_CHECK=1` 隔离网络。

## 3. Agent Desktop 和嵌入式终端

- 必须是真正的交互终端，stdin/stdout 为 TTY；通过普通管道执行只能使用 `--doctor`、`--stats` 等非交互入口。
- 终端至少 40 列、6 行。先保证键盘焦点位于终端面板，再测试输入。
- 宿主可能拦截功能键；默认学习自动切换题型，设置可使用 Esc 菜单。输入为空时 `?` 也可打开菜单。
- 不要通过修改宿主全局快捷键来掩盖程序问题。先区分按键未送达、TERM 不匹配、窗口尺寸不足和程序状态错误。
- 长释义和例句可用上下键、PageUp/PageDown 翻阅。缩放后应继续保留题目、输入和评分状态。
- 在不同宿主中记录实际系统、Python、终端类型、尺寸和复现操作。一次 Linux PTY 通过不等于所有 Desktop 端均已人工验收。

## 4. 内部接口与修改入口

以下是当前实现的 Python 接口，不是独立发布的稳定 SDK。模块位于对应版本目录；修改时同步测试、快照 schema 和文档。

| 需求 | 修改入口 | 需要保持的约束 |
|---|---|---|
| 新词库/词库切换 | `library.Catalog`、`StudyLibrary` | ID、路径范围、有效词数、SHA、独立 profile；详见 [DICTIONARIES.md](DICTIONARIES.md) |
| 释义显示/题型 | `quiz.display_meanings`、`QuestionFactory.make`、`is_correct` | 不丢已有义项；干扰项排除共享义项；缺内容明确回退 |
| 练习顺序/恢复 | `practice.PracticeRound` | `submit/reveal/skip/snapshot/restore`；评分 event_id 幂等，当前输入可恢复 |
| 复习算法 | `scheduler.StudyPlanner` | `note/due_cards/stats/get_intervals/set_intervals`；数据库迁移与旧记录可追溯 |
| 目标/增量/提醒配置 | `routine.Routine` | 严格类型、原子保存、旧字段缺省；当日实际到期量规则 |
| UI 与快捷键 | `ielts.TerminalStudy` | 40×6、焦点、翻页、题目不被提醒打断；不要依赖 F10 |
| 发音后端 | `pronunciation.Pronouncer` | `speak/cancel/poll_message/close`；取消旧请求、避免叠音、失败不阻断练习 |
| 系统兼容 | `portable_compat`、`windows_terminal.py` | POSIX/Windows 文件锁、权限、信号；Windows V3.3.1-beta.1 使用标准库 VT/native console，不能只检查模块能 import |
| 外层部署 | `launcher.py` | 相对路径、数据隔离、`--doctor`、`--portable` 与选版本参数 |
| 稳定版更新 | `updater.UpdateManager` | `check_async/install_async/poll/activate_ready/active_launcher/close`；检查、下载和激活分开，保留证书校验与原包清单绑定，切换前备份并释放学习锁 |

### 题目协议

`QuestionFactory.make(word_name, mode)` 返回字典，主要字段为 `mode`、`word`、`prompt`、`answer`、`options`、`correct_indices`。例句可能带 `translation`，回退可能带 `reason`。

加入题型时同时检查 `MODE_LABELS`、题型工厂、判分、调度器可接受模式、轮次恢复校验、音频提示策略和 UI。不要仅增加一个菜单项。缺少例句时，不要用空模板假装有内容。

### 评分与存储协议

- `learn`：基础学习；`review`：正式复习；`extra`：增量/穿插例句；`repair`：错题跟打补练。
- 增量正确不延长原有复习间隔，不充当每日新词/复习目标。错误或提示仍可触发短期重测。
- 同一 event_id 不得重复记分。恢复旧快照时，不改变原题选项和答案；更新释义提示也不能使既有评分错位。
- `StateStore` 负责 JSON 文件锁与原子替换；SQLite 保存事件和排期。不要直接编辑正在使用的数据文件。
- 默认使用单个进度目录的单会话锁；不同版本或不同使用者应使用不同目录。

## 5. 推荐优化顺序和验收接口

| 优化方向 | 当前缺口 | 最小验收 |
|---|---|---|
| 跨平台交互 | Windows/macOS 和不同宿主仍需要人工交互覆盖 | 非交互 smoke + 真正的菜单、输入、缩放、恢复测试 |
| 例句扩充 | 124 词覆盖有限 | 精确目标词、一个空格、正确词形、自然搭配、中文译文、独立抽查 |
| 词库管理 | 添加新数据仍需更新 catalog | 新 ID 导入、路径边界、SHA/词数校验、旧 profile 保留 |
| 判分改进 | 当前不理解同义词或语义等价 | 明确可接受答案列表；不要把任意相近词都判对 |
| 调度算法 | 当前为简化间隔规则 | 时间边界/错误/提示/增量隔离测试；新算法不得覆盖原历史 |
| 版本管理 | 入口、目录和打包工具中有版本映射 | 集中版本元数据，旧包与数据兼容测试，更新指针/备份/校验流程，避免只改 VERSION |
| 发音适配 | 依赖 ffplay 与首次联网 | mock 后端测试、实际播放单独验证、取消/失败/缓存边界 |

这些是优化入口，不代表已经实现了词库热加载、语义判分、FSRS、HTTP/IPC 服务或完整 Agent 插件协议。若新增此类接口，先写协议和兼容性测试，再调整文档中的能力声明。

## 6. 验证与交付

```sh
python3 -m pip install -r requirements-test.txt
cd apps/v3.3.1-beta.1
IELTS_DISABLE_UPDATE_CHECK=1 python3 -B -m unittest discover -q
```

测试应使用临时目录、固定时钟和假发声器。不能为了展示通过而自动填写用户真实题目、修改真实学习成绩、播放声音或启用系统提醒。

跨平台真实终端回归使用 `tools/terminal_smoke.py`，流程和可下载的离线回放见 [TERMINAL_TESTING.md](TERMINAL_TESTING.md)。该套场景禁止替换应用的输入、绘制和 curses 入口；应保留失败产物，不能用管道子进程代替 PTY/ConPTY 来报告交互通过。

发布包由仓库根目录运行：

```sh
python3 tools/package_release.py dist/IELTS-CLI-Portable-3.3.1-beta.1.zip
```

该命令只打包 Git 跟踪文件并生成清单，不覆盖已有 ZIP。文件需先纳入 Git，再打包。对输出 ZIP 另做解压、`--verify` 和独立启动检查；不能用工作目录通过代替交付副本通过。

提交前检查 `.gitignore`、`git diff --check`、来源许可证和变更范围。发布到远端必须已有用户授权，并核对目标仓库与账号；不要把当前机器的默认账号当成用户指定账号。
