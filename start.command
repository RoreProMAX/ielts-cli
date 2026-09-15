#!/bin/sh
# 从任何当前工作目录启动；不写 PATH 或系统配置。
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1
if command -v python3 >/dev/null 2>&1; then
    runner=python3
elif command -v python >/dev/null 2>&1; then
    runner=python
else
    printf '%s\n' '请安装 Python 3.10 或更新版本，详见 docs/部署指南.html。'
    exit 2
fi
if ! "$runner" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
    printf '%s\n' '需要 Python 3.10 或更新版本。'
    exit 2
fi
export PYTHONUTF8=1
exec "$runner" -X utf8 -B "$script_dir/launcher.py" "$@"
