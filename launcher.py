#!/usr/bin/env python3
"""可移动的三版本入口；检查环境、验证文件，并显式选择数据位置。"""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import runpy
import shutil
import sqlite3
import sys

ROOT = Path(__file__).resolve().parent
CURRENT_VERSION = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
BUNDLE_VERSION = CURRENT_VERSION + '-public.1'
APP_VERSIONS = {'1': '1.0.0', '2': '2.0.0', '3': CURRENT_VERSION}


def uses_windows_vt(version):
    return sys.platform == 'win32' and (ROOT / 'apps' / ('v' + version) / 'windows_terminal.py').is_file()


def default_data_root():
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'IELTS-CLI-Portable'
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/IELTS-CLI-Portable'
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'ielts-cli-portable'


def default_cache_home():
    if sys.platform == 'win32':
        return default_data_root() / 'cache'
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Caches'
    return Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache'))


def locations(options):
    version = APP_VERSIONS[options.app_version]
    data_root = ROOT / 'user-data' if options.portable else default_data_root()
    data_dir = options.data_dir or data_root / 'versions' / version
    cache_home = ROOT / 'user-data/cache' if options.portable else default_cache_home()
    if os.environ.get('IELTS_UPDATE_CACHE_HOME'):
        cache_home = Path(os.environ['IELTS_UPDATE_CACHE_HOME'])
    return version, Path(data_dir).expanduser().resolve(), cache_home.expanduser().resolve()


def verify_bundle():
    path = ROOT / 'BUNDLE_MANIFEST.json'
    try:
        manifest = json.loads(path.read_text(encoding='utf-8'))
        failures = []
        for name, expected in manifest['files'].items():
            item = ROOT / name
            if Path(name).is_absolute() or '..' in Path(name).parts or not item.is_file():
                failures.append({'file': name, 'error': 'missing_or_invalid_path'})
            elif item.is_symlink() or hashlib.sha256(item.read_bytes()).hexdigest() != expected:
                failures.append({'file': name, 'error': 'sha256_mismatch'})
        return {'ok': not failures, 'files_checked': len(manifest['files']), 'failures': failures}
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {'ok': False, 'error': str(error)}


def doctor(options):
    version, data_dir, cache_home = locations(options)
    checks = []
    checks.append({'name': 'Python >= 3.10', 'ok': sys.version_info >= (3, 10), 'detail': sys.version.split()[0]})
    if uses_windows_vt(version):
        windows = sys.getwindowsversion()
        curses_ok = windows.major >= 10 and windows.build >= 17763
        backend = 'windows-vt'
        terminal_detail = 'Windows VT / Win32 console (Windows 10 build 17763 or later)'
    else:
        try:
            import curses
            curses_ok = hasattr(curses, 'wrapper') and hasattr(curses, 'getsyx')
        except ImportError:
            curses_ok = False
        backend = 'curses'
        terminal_detail = 'Windows: py -3 -m pip install -r requirements-windows.txt' if not curses_ok else 'available'
    checks.append({'name': 'terminal backend', 'ok': curses_ok, 'detail': terminal_detail})
    checks.append({'name': 'SQLite >= 3.24', 'ok': sqlite3.sqlite_version_info >= (3, 24, 0), 'detail': sqlite3.sqlite_version})
    checks.append({'name': 'application', 'ok': (ROOT / 'apps' / ('v' + version) / 'ielts.py').is_file(), 'detail': version})
    return {
        'bundle_version': BUNDLE_VERSION,
        'platform': sys.platform,
        'python_executable': sys.executable,
        'terminal_backend': backend,
        'core_ready': all(item['ok'] for item in checks),
        'checks': checks,
        'data_directory': str(data_dir),
        'audio_cache': str(cache_home / 'ielts-cli/audio'),
        'portable_mode': options.portable,
        'personal_data_import': 'disabled',
        'ffplay': shutil.which('ffplay'),
        'audio_note': 'ffplay 为发音所需；缺失时仍可跟打、默写和做选择题。首次获取某词读音需要联网。',
        'background_reminder_backend': 'systemd user timer (optional)' if sys.platform.startswith('linux') and shutil.which('systemctl') and shutil.which('notify-send') else 'unavailable; in-app reminders remain available',
        'terminal_interactive': sys.stdin.isatty() and sys.stdout.isatty(),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False, description='IELTS CLI 通用分享包入口；其它参数传给所选应用版本。')
    parser.add_argument('--app-version', choices=('1', '2', '3'), default='3', help='选择应用版本，默认3')
    parser.add_argument('--portable', action='store_true', help='把进度和音频缓存保存在本文件夹的 user-data 中')
    parser.add_argument('--data-dir', type=Path, help='手动指定进度目录，优先于默认和便携路径')
    parser.add_argument('--doctor', action='store_true', help='只检查环境与显示数据路径，不创建学习数据')
    parser.add_argument('--verify', action='store_true', help='核对分享包文件 SHA-256')
    parser.add_argument('--launcher-help', action='help', help='显示启动器选项')
    options, app_args = parser.parse_known_args(argv)
    if options.doctor or options.verify:
        report = {}
        if options.verify:
            report['integrity'] = verify_bundle()
        if options.doctor:
            report['environment'] = doctor(options)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get('integrity', {}).get('ok', True) and report.get('environment', {}).get('core_ready', True) else 1
    if sys.version_info < (3, 10):
        print('请先安装 Python 3.10 或更新版本。', file=sys.stderr)
        return 2
    version, data_dir, cache_home = locations(options)
    if not uses_windows_vt(version) and importlib.util.find_spec('curses') is None:
        print('缺少 curses。Windows 请运行：py -3 -m pip install -r requirements-windows.txt', file=sys.stderr)
        return 2
    if any(arg == '--reminders' or arg.startswith('--reminders=') for arg in app_args) and not sys.platform.startswith('linux'):
        print('退出后的后台提醒当前只支持 Linux systemd；程序内提醒仍可使用。', file=sys.stderr)
        return 2
    app_root = ROOT / 'apps' / ('v' + version)
    sys.dont_write_bytecode = True
    os.environ['XDG_CACHE_HOME'] = str(cache_home)
    if sys.platform == 'win32':
        os.environ.setdefault('TERM', 'xterm-256color')
    sys.path.insert(0, str(app_root))
    # 分享入口只使用所选数据目录，不隐式读取接收者电脑上的其它学习记录。
    if '--no-import' not in app_args:
        app_args.append('--no-import')
    sys.argv = [str(app_root / 'ielts.py'), '--data-dir', str(data_dir)] + app_args
    try:
        runpy.run_path(str(app_root / 'ielts.py'), run_name='__main__')
    except ImportError as error:
        print('运行依赖缺失：%s。请运行启动器 --doctor 检查。' % error, file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    raise SystemExit(main())
