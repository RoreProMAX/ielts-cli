#!/usr/bin/env python3
"""Read real curses and Win32 console cell positions for fixed Unicode samples."""

from __future__ import annotations

import argparse
import ctypes
import importlib.metadata
import json
import locale
import os
from pathlib import Path
import platform
import sys
import tempfile
import time

from terminal_driver import TerminalSession


def console_cells():
    from ctypes import wintypes

    class Coord(ctypes.Structure):
        _fields_ = [('X', ctypes.c_short), ('Y', ctypes.c_short)]

    class Rect(ctypes.Structure):
        _fields_ = [('Left', ctypes.c_short), ('Top', ctypes.c_short),
                    ('Right', ctypes.c_short), ('Bottom', ctypes.c_short)]

    class Info(ctypes.Structure):
        _fields_ = [('size', Coord), ('cursor', Coord), ('attributes', wintypes.WORD),
                    ('window', Rect), ('maximum', Coord)]

    class Char(ctypes.Union):
        _fields_ = [('unicode', ctypes.c_wchar), ('ascii', ctypes.c_char)]

    class Cell(ctypes.Structure):
        _fields_ = [('char', Char), ('attributes', wintypes.WORD)]

    api = ctypes.WinDLL('kernel32', use_last_error=True)
    # PDCurses activates its own screen buffer. The inherited STD_OUTPUT_HANDLE
    # can still name the original blank buffer, so open the active CONOUT$.
    api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                               ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    api.CreateFileW.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    handle = api.CreateFileW('CONOUT$', 0x80000000, 3, None, 3, 0, None)
    if handle in (None, wintypes.HANDLE(-1).value):
        raise OSError(ctypes.get_last_error(), 'Opening active CONOUT$ failed')
    api.GetConsoleScreenBufferInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Info)]
    api.GetConsoleScreenBufferInfo.restype = wintypes.BOOL
    try:
        info = Info()
        if not api.GetConsoleScreenBufferInfo(handle, ctypes.byref(info)):
            raise OSError(ctypes.get_last_error(), 'GetConsoleScreenBufferInfo failed')
        api.ReadConsoleOutputW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Cell), Coord, Coord, ctypes.POINTER(Rect)]
        api.ReadConsoleOutputW.restype = wintypes.BOOL
        cells = (Cell * 12)()
        region = Rect(0, info.window.Top, 11, info.window.Top)
        if not api.ReadConsoleOutputW(handle, cells, Coord(12, 1), Coord(0, 0), ctypes.byref(region)):
            raise OSError(ctypes.get_last_error(), 'ReadConsoleOutputW failed')
        return {'read_handle': 'CONOUT$ active screen buffer',
                'cursor_x': info.cursor.X, 'cursor_y': info.cursor.Y,
                'cells': [{'x': index, 'text': cell.char.unicode, 'attributes': cell.attributes}
                          for index, cell in enumerate(cells)]}
    finally:
        api.CloseHandle(handle)


def child(destination, utf8, backend):
    if backend == 'windows-vt':
        root = Path(__file__).resolve().parents[1]
        version = (root / 'VERSION').read_text(encoding='utf-8').strip()
        sys.path.insert(0, str(root / 'apps' / ('v' + version)))
        import windows_terminal as curses
    else:
        import curses
    if utf8:
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        assert api.SetConsoleCP(65001) and api.SetConsoleOutputCP(65001)
        locale.setlocale(locale.LC_CTYPE, '.UTF-8')
    else:
        locale.setlocale(locale.LC_ALL, '')
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    result = {'platform': platform.system() + ' ' + platform.release(), 'backend': backend,
              'windows_curses': importlib.metadata.version('windows-curses'),
              'mode': 'explicit-utf8-console' if utf8 else 'default-console',
              'input_codepage': api.GetConsoleCP(), 'output_codepage': api.GetConsoleOutputCP(),
              'locale': locale.setlocale(locale.LC_CTYPE), 'measurements': []}

    def run(screen):
        result['active_input_codepage'] = api.GetConsoleCP()
        result['active_output_codepage'] = api.GetConsoleOutputCP()
        curses.raw()
        curses.noecho()
        screen.keypad(True)
        for sample, expected in [('ABC', 3), ('A中B', 4), ('中文', 4), ('今日任务', 8)]:
            screen.erase()
            screen.addstr(0, 0, sample)
            cursor = screen.getyx()
            screen.refresh()
            try:
                native = console_cells()
                if sample == 'ABC':
                    native['ascii_control_matches'] = ''.join(cell['text'] for cell in native['cells'][:3]) == 'ABC'
            except OSError as error:
                native = {'read_error': str(error)}
            result['measurements'].append({'sample': sample, 'expected_wcswidth_columns': expected,
                                            'curses_cursor_y': cursor[0], 'curses_cursor_x': cursor[1],
                                            'native_console': native})
            time.sleep(0.15)
    curses.wrapper(run)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--child-result', type=Path)
    parser.add_argument('--utf8-console', action='store_true')
    parser.add_argument('--backend', choices=('pdcurses', 'windows-vt'), default='pdcurses')
    args = parser.parse_args()
    if args.child_result:
        return child(args.child_result, args.utf8_console, args.backend)
    if sys.platform != 'win32':
        parser.error('The native Win32 width probe must run on Windows')
    if args.output is None:
        parser.error('--output is required')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        parser.error('Output must be empty; prior evidence is preserved')
    results, events = [], []
    with tempfile.TemporaryDirectory(prefix='ielts-width-probe-') as temporary:
        for backend, utf8 in (('pdcurses', False), ('pdcurses', True), ('windows-vt', False), ('windows-vt', True)):
            destination = Path(temporary) / (backend + ('-utf8.json' if utf8 else '-default.json'))
            command = [sys.executable, '-X', 'utf8', '-B', str(Path(__file__).resolve()),
                       '--child-result', str(destination), '--backend', backend]
            if utf8:
                command.append('--utf8-console')
            allowed = ('PATH', 'SystemRoot', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT')
            env = {key: os.environ[key] for key in allowed if key in os.environ}
            env.update(TERM='xterm-256color', PYTHONUTF8='1')
            session = TerminalSession(command, cwd=Path(temporary), env=env, rows=8, cols=40)
            started = time.monotonic()
            try:
                while time.monotonic() - started < 15 and session.is_alive():
                    chunk = session.read(timeout=0.1)
                    if chunk:
                        events.append({'backend': backend, 'mode': 'utf8' if utf8 else 'default', 'output': chunk})
                if session.is_alive():
                    raise AssertionError('Native width probe did not finish')
                session.close()
                assert session.exit_code() == 0, 'Native width probe failed with status ' + str(session.exit_code())
                results.append(json.loads(destination.read_text(encoding='utf-8')))
            except Exception as error:
                results.append({'backend': backend, 'mode': 'utf8' if utf8 else 'default', 'probe_error': str(error)})
            finally:
                session.close()
    completed = all('measurements' in result for result in results)
    vt_passed = completed and all(
        measurement['curses_cursor_x'] == measurement['expected_wcswidth_columns']
        and measurement['native_console'].get('cursor_x') == measurement['expected_wcswidth_columns']
        for result in results if result['backend'] == 'windows-vt'
        for measurement in result['measurements'])
    (output / 'width-probe.json').write_text(json.dumps({'diagnostic_completed': completed, 'windows_vt_width_passed': vt_passed, 'variants': results}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (output / 'width-probe-vt.jsonl').write_text(''.join(json.dumps(event, ensure_ascii=False) + '\n' for event in events), encoding='utf-8')
    print(json.dumps(results, ensure_ascii=False))
    return 0 if vt_passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
