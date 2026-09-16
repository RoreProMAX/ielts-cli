# 接口参考

本项目对外接口是启动脚本和 Python CLI 参数。没有已承诺的 IPC、HTTP、插件协议或模型 API。

## 启动器参数

`launcher.py` 支持：

| 参数 | 作用 |
|---|---|
| `--app-version 1\|2\|3` | 选择应用版本，默认 `3`（V3.3.1 稳定版） |
| `--portable` | 将进度和音频缓存放入当前目录 `user-data/` |
| `--data-dir PATH` | 指定进度目录 |
| `--doctor` | 检查运行环境和解析后的路径，不创建学习数据 |
| `--verify` | 按 `BUNDLE_MANIFEST.json` 核对发布包 SHA-256 |
| `--launcher-help` | 显示入口参数帮助 |

启动器会把其余参数传给所选应用版本。

## V3 CLI 参数

常用参数包括 `--learn`、`--review`、`--daily`、`--incremental`、`--dictionary ID`、`--chapter N`、`--list-dictionaries`、`--stats`、`--question-mode MODE`、`--review-intervals 1,3,7,14,30`。

设置参数包括 `--configure --new-goal N`、`--review-goal N`、`--batch-size N`、`--reminder-minutes N`、`--extra-after-daily on|off`、`--examples-during-learning on|off`、`--auto-update-check on|off`、`--update-channel stable|beta`。`--update-status` 只读显示版本与 channel；`--no-update` 仅本次跳过检查；提醒管理使用 `--reminders enable|disable|status`，其退出后后台实现仅支持 Linux。默认 channel 为 `stable`；beta 仅接受高于当前版本的 stable/beta，不降级。

示例：

```sh
python3 launcher.py --app-version 3 --list-dictionaries
python3 launcher.py --app-version 3 --learn --dictionary cet4 --chapter 2
python3 launcher.py --app-version 3 --configure --new-goal 20 --review-goal 30
python3 launcher.py --app-version 3 --incremental
```

参数的最终行为以对应版本 `ielts.py --help` 和实际源码为准。Release 包才有 `--verify` 所需的清单；源码 checkout 只适合运行 `--doctor`。

## 模块扩展与优化

内部 Python 接口、评分上下文、快照兼容要求、发音适配点和优化验收标准见 [forAgent 部署与优化指南](FOR_AGENTS.md)。这些是源码扩展入口，不是已发布的 HTTP/IPC 服务或稳定 SDK。
