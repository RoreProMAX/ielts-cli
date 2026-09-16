#!/usr/bin/env python3
"""在真实 PTY/ConPTY 中回归终端操作，并保存可离线查看的脱敏回放。"""

from __future__ import annotations

import argparse
from contextlib import closing
import datetime
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import sys
import tempfile
import time
import traceback

import pyte
from wcwidth import wcwidth

from terminal_driver import TerminalSession
from terminal_report import write_report


ROOT = Path(__file__).resolve().parents[1]
MAX_CAPTURE_CHARS = 2_000_000
TIMEOUT = 18


class ReplyingScreen(pyte.Screen):
    """按捕获的真实光标位置回复标准终端查询，不制造应用屏幕。"""

    def __init__(self, columns, lines, reply):
        self.reply = reply
        self.orphan_cell_fallbacks = 0
        super().__init__(columns, lines)

    def write_process_input(self, data):
        self.reply(data)

    @property
    def display(self):
        try:
            return super().display
        except IndexError:
            # pyte 0.8.2 在宽字符左半格被 ASCII 覆盖后保留空右半格，
            # 例如“中文\rA”会令其 display 崩溃。仅把这种孤立空格显示为空白；
            # 原始 VT 输出完整保留，不改变应用输出、输入或终端缓冲区。
            self.orphan_cell_fallbacks += 1
            result = []
            for y in range(self.lines):
                line, continuation = [], False
                for x in range(self.columns):
                    if continuation:
                        continuation = False
                        continue
                    data = self.buffer[y][x].data or ' '
                    continuation = wcwidth(data[0]) == 2
                    line.append(data)
                result.append(''.join(line))
            return result


class Scenario:
    def __init__(self, output, temporary):
        self.output = output
        self.temporary = temporary.resolve()
        self.bundle = self.temporary / '程序 CLI with spaces'
        self.profile = self.temporary / '学习进度 with spaces'
        self.version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
        self.session = None
        self.screen = None
        self.stream = None
        self.rows, self.cols = 24, 80
        self.started = time.monotonic()
        self.step = 'prepare'
        self.steps = []
        self.frames = []
        self.events = []
        self.capture_chars = 0
        self.last_lines = None
        self.last_capture_time = 0.0
        self.backends = []
        self.exit_codes = []
        self.redactions = sorted([
            (str(temporary), '<TEMP>'), (str(self.temporary), '<TEMP>'), (str(ROOT.resolve()), '<REPO>'),
            (str(Path.home()), '<HOME>'), (str(Path.home().resolve()), '<HOME>'),
        ], key=lambda item: len(item[0]), reverse=True)

    def redact(self, text):
        result = str(text)
        for original, replacement in self.redactions:
            if original:
                result = result.replace(original, replacement)
                result = result.replace(original.replace('\\', '/'), replacement)
                result = result.replace(original.replace('\\', '\\\\'), replacement)
        return result

    def event(self, kind, **details):
        self.events.append(dict(time=round(time.monotonic() - self.started, 3),
                                kind=kind, step=self.step, **details))

    def capture(self, force=False):
        if self.screen is None:
            return
        lines = [self.redact(line.rstrip()) for line in self.screen.display]
        identity = (self.step, self.rows, self.cols, tuple(lines))
        if force or identity != self.last_lines:
            moment = time.monotonic()
            if not force and moment - self.last_capture_time < 0.05:
                return
            if len(self.frames) >= 4000:
                raise RuntimeError('Terminal frame capture exceeded its bounded size')
            self.frames.append({'time': round(time.monotonic() - self.started, 3),
                                'step': self.step, 'rows': self.rows, 'cols': self.cols,
                                'lines': lines})
            self.last_lines = identity
            self.last_capture_time = moment

    def prepare(self):
        self.bundle.mkdir()
        for name in ('launcher.py', 'VERSION'):
            shutil.copy2(ROOT / name, self.bundle / name)
        app_source = ROOT / 'apps' / ('v' + self.version)
        app_target = self.bundle / 'apps' / ('v' + self.version)
        shutil.copytree(app_source, app_target, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        sys.path.insert(0, str(app_target))
        from audio_settings import AudioSettings
        from library import Catalog, StudyLibrary
        from routine import Routine
        from scheduler import StudyPlanner
        from study import StateStore
        from updater import UpdateManager
        with StateStore(self.profile) as store:
            library = StudyLibrary(Catalog(app_target / 'data/catalog.json'), store)
            library.save()
            self.first_word = library.session.words[0]['name']
            self.second_word = library.session.words[1]['name']
            assert self.first_word == 'cancel', 'The fixed example fixture requires cancel'
            planner = StudyPlanner(self.profile)
            try:
                planner.note('ielts', self.first_word, 'copy', True, context='learn',
                             event_id='terminal-fixture-seed', now=time.time() - 3600)
                assert planner.due_cards(limit=1), 'Review scenario requires an actually due fixture card'
            finally:
                planner.close()
            Routine(self.profile).update(new_goal=999, review_goal=999, batch_size=2,
                                         extra_after_daily=False, examples_during_learning=False,
                                         reminder_minutes=0)
            AudioSettings(self.profile).set_auto(False)
            UpdateManager(self.profile, self.version).set_auto_check(True)
        self.learning_draft = self.second_word[:3]
        self.extra_draft = self.first_word[:2]
        self.event('fixture', profile='<TEMP>/学习进度 with spaces',
                   application='apps/v' + self.version, data='synthetic vocabulary progress only')

    def protocol_reply(self, data):
        self.event('terminal_reply', data=self.redact(data))
        if self.session and self.session.is_alive():
            self.session.write(data)

    def start(self, rows=24, cols=80):
        self.rows, self.cols = rows, cols
        allowed = ('PATH', 'SystemRoot', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'TZ')
        env = {key: os.environ[key] for key in allowed if key in os.environ}
        isolated_home = self.temporary / 'isolated-home'
        isolated_home.mkdir(exist_ok=True)
        env.update(HOME=str(isolated_home), USERPROFILE=str(isolated_home),
                   LOCALAPPDATA=str(isolated_home / 'AppData/Local'),
                   APPDATA=str(isolated_home / 'AppData/Roaming'),
                   TEMP=str(self.temporary), TMP=str(self.temporary), TMPDIR=str(self.temporary),
                   TERM='xterm-256color', PYTHONUTF8='1',
                   PYTHONDONTWRITEBYTECODE='1', IELTS_DISABLE_UPDATE_CHECK='1',
                   XDG_CACHE_HOME=str(self.temporary / 'audio-cache'),
                   PYTHONPATH='')
        if sys.platform != 'win32':
            env.update(LANG='en_US.UTF-8' if sys.platform == 'darwin' else 'C.UTF-8',
                       LC_ALL='en_US.UTF-8' if sys.platform == 'darwin' else 'C.UTF-8')
        argv = [sys.executable, '-X', 'utf8', '-B', str(self.bundle / 'launcher.py'),
                '--data-dir', str(self.profile), '--no-import', '--learn', '--no-update']
        self.screen = ReplyingScreen(cols, rows, self.protocol_reply)
        self.stream = pyte.Stream(self.screen)
        self.session = TerminalSession(argv, cwd=self.temporary, env=env, rows=rows, cols=cols)
        self.backends.append(self.session.backend)
        self.event('start', backend=self.session.backend, rows=rows, cols=cols,
                   argv=['python', 'launcher.py', '--data-dir', '<TEMP>/学习进度 with spaces',
                         '--no-import', '--learn', '--no-update'])

    def pump(self, duration=0.15):
        end = time.monotonic() + duration
        while time.monotonic() < end:
            chunk = self.session.read(timeout=min(0.1, max(0.0, end - time.monotonic())))
            if chunk:
                self.capture_chars += len(chunk)
                if self.capture_chars > MAX_CAPTURE_CHARS:
                    raise RuntimeError('Terminal output exceeded the bounded capture size')
                self.event('output', data=self.redact(chunk))
                self.stream.feed(chunk)
                self.capture()

    def wait(self, predicate, description, timeout=TIMEOUT):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.pump()
            if predicate():
                self.capture(force=True)
                return
            if not self.session.is_alive():
                self.pump(0.5)
                raise AssertionError(description + ': child exited before the expected state')
        self.capture(force=True)
        raise AssertionError(description + ': timed out')

    def header(self):
        return self.screen.display[0]

    def activity(self, name):
        return self.header().startswith('雅思随手练 v' + self.version + ' ' + name)

    def draft_visible(self, draft):
        return any(line.startswith(prefix + draft) for line in self.screen.display
                   for prefix in ('答案 > ', '补练 > ', '输入 > '))

    def snapshot(self, kind='learn'):
        filename = {'learn': 'learn_round-ielts.json', 'extra': 'extra_round.json',
                    'review': 'active_round.json'}[kind]
        try:
            return json.loads((self.profile / filename).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def setting(self):
        return json.loads((self.profile / 'update_settings.json').read_text(encoding='utf-8'))['auto_check']

    def update_channel(self):
        return json.loads((self.profile / 'update_settings.json').read_text(encoding='utf-8')).get('channel')

    def write(self, text, label=None):
        self.event('input', data=text, label=label or text)
        self.session.write(text)

    def menu(self, number, expected_header):
        self.write('\x1b', 'Esc')
        self.wait(lambda: '学习菜单' in self.header(), 'open main menu')
        self.write(str(number) + '\r', 'menu ' + str(number) + ' then Enter')
        self.wait(lambda: self.activity(expected_header) if expected_header in ('学习', '增量练习', '复习') else expected_header in self.header(), 'select menu ' + str(number))

    def check_step(self, name, action):
        self.step = name
        self.event('step_start')
        try:
            action()
            self.capture(force=True)
            self.steps.append({'name': name, 'status': 'passed'})
            self.event('step_pass')
        except Exception as error:
            self.capture(force=True)
            self.steps.append({'name': name, 'status': 'failed', 'details': self.redact(error)})
            self.event('step_fail', error=self.redact(error))
            raise

    def launch_and_answer(self):
        self.start()
        self.wait(lambda: self.activity('学习') and self.snapshot().get('question', {}).get('mode') == 'copy', 'initial word-learning screen')
        self.write(self.first_word + '\r', 'answer fixed first word')
        self.wait(lambda: self.snapshot().get('phase') == 'done', 'correct spelling is persisted')
        self.write('\r', 'Enter next question')
        self.wait(lambda: self.snapshot().get('current', {}).get('word') == self.second_word, 'next word')
        self.write(self.learning_draft, 'type fixed partial word')
        self.wait(lambda: self.snapshot().get('draft') == self.learning_draft, 'learning draft persisted')
        self.learning_before = self.snapshot()

    def extra_round_trip(self):
        self.menu(9, '增量练习')
        self.wait(lambda: self.snapshot('extra').get('mode') in ('cloze', 'collocation'), 'real example question')
        self.write(self.extra_draft, 'type fixed partial example answer')
        self.wait(lambda: self.snapshot('extra').get('draft') == self.extra_draft, 'example draft persisted')
        self.extra_before = self.snapshot('extra')
        self.menu(2, '学习')
        self.wait(lambda: self.activity('学习') and self.draft_visible(self.learning_draft), 'word screen is restored')
        for key in ('draft', 'event_id', 'current', 'question'):
            assert self.snapshot()[key] == self.learning_before[key], 'Learning bookmark changed at ' + key
            assert self.snapshot('extra')[key] == self.extra_before[key], 'Example bookmark changed at ' + key

    def update_menu(self):
        self.menu(10, '版本与更新')
        self.write('1\r', 'toggle automatic stable check')
        self.wait(lambda: self.setting() is False and '关' in '\n'.join(self.screen.display), 'update check is disabled')
        if self.update_channel() is not None:
            assert self.update_channel() == 'stable', 'Beta updates must be opt-in'
            self.write('2\r', 'explicitly choose beta update channel')
            self.wait(lambda: self.update_channel() == 'beta' and '更新通道：beta' in '\n'.join(self.screen.display), 'beta channel is explicitly selected')
            self.write('2\r', 'switch update channel back to stable')
            self.wait(lambda: self.update_channel() == 'stable' and '更新通道：稳定版' in '\n'.join(self.screen.display), 'stable channel restored without a download')
            self.event('beta_channel_controls', default='stable', opt_in_verified=True,
                       returned_to_stable=True, automatic_download=False)
        self.write('\x1b', 'Esc return to current question')
        self.wait(lambda: self.activity('学习') and self.draft_visible(self.learning_draft), 'return from settings preserves draft')
        assert self.snapshot()['event_id'] == self.learning_before['event_id']

    def function_keys(self):
        self.write('\x1b[23~', 'F11 via terminal input sequence')
        self.wait(lambda: '今日任务' in self.header(), 'F11 opens daily panel')
        self.write('\x1b[23~', 'F11 close daily panel')
        self.wait(lambda: self.activity('学习'), 'F11 returns to learning')
        self.write('\x1b[20~', 'F9 via terminal input sequence')
        self.wait(lambda: self.activity('复习'), 'F9 opens due review')
        self.write('\x1b[20~', 'F9 pause review')
        self.wait(lambda: self.activity('学习') and self.draft_visible(self.learning_draft), 'F9 returns with draft')

    def resize(self, rows, cols):
        self.event('resize', rows=rows, cols=cols)
        self.rows, self.cols = rows, cols
        self.screen.resize(lines=rows, columns=cols)
        self.session.resize(rows, cols)
        self.pump(0.25)

    def resize_round_trip(self):
        self.resize(5, 35)
        self.wait(lambda: '终端至少需要' in self.header(), 'undersized terminal warning')
        self.resize(6, 40)
        self.wait(lambda: self.activity('学习') and self.draft_visible(self.learning_draft), 'minimum-size terminal redraw')
        self.menu(10, '版本与更新')
        self.wait(lambda: '关' in '\n'.join(self.screen.display), 'update menu at 40x6')
        self.write('\x1b', 'Esc return at minimum size')
        self.wait(lambda: self.draft_visible(self.learning_draft), 'draft at minimum size')
        self.resize(24, 80)
        self.wait(lambda: self.activity('学习') and self.draft_visible(self.learning_draft), 'restore normal size')

    def quit(self):
        self.write('\x11', 'Ctrl+Q save and exit')
        deadline = time.monotonic() + TIMEOUT
        while time.monotonic() < deadline and self.session.is_alive():
            self.pump()
        assert not self.session.is_alive(), 'Ctrl+Q did not terminate the created process'
        # ConPTY may deliver the final text after reporting process termination.
        self.pump(0.6)
        self.session.close()
        code = self.session.exit_code()
        self.exit_codes.append(code)
        assert code == 0, 'Interactive child exit status was ' + str(code)
        self.session = None
        assert self.snapshot()['draft'] == self.learning_draft

    def restart_and_resume(self):
        self.start(6, 40)
        self.wait(lambda: self.activity('学习') and self.draft_visible(self.learning_draft), 'restart preserves learning draft')
        assert self.snapshot()['event_id'] == self.learning_before['event_id']
        assert self.setting() is False
        self.menu(9, '增量练习')
        self.wait(lambda: self.draft_visible(self.extra_draft), 'restart preserves example draft')
        assert self.snapshot('extra')['event_id'] == self.extra_before['event_id']
        self.menu(2, '学习')
        self.wait(lambda: self.draft_visible(self.learning_draft), 'restart return to word learning')

    def verify_storage(self):
        with closing(sqlite3.connect(self.profile / 'learning.sqlite3')) as database:
            events = database.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        assert events == 2, 'Navigation changed the number of scored attempts'
        assert self.snapshot('extra')['draft'] == self.extra_draft
        assert self.snapshot()['draft'] == self.learning_draft
        assert not (self.profile / 'updates' / 'active.json').exists()
        assert not (self.profile / 'updates' / 'downloads').exists()
        self.event('storage_check', scored_events=events, learning_draft_preserved=True,
                   example_draft_preserved=True, update_downloaded=False)

    def execute(self):
        failure = None
        try:
            for name, action in [
                ('prepare-isolated-fixture', self.prepare),
                ('launch-answer-and-partial-word', self.launch_and_answer),
                ('example-to-word-round-trip', self.extra_round_trip),
                ('real-update-menu-and-setting', self.update_menu),
                ('F11-and-F9-through-terminal', self.function_keys),
                ('resize-normal-small-minimum-normal', self.resize_round_trip),
                ('save-and-exit', self.quit),
                ('restart-and-resume-both-drafts', self.restart_and_resume),
                ('save-and-exit-again', self.quit),
                ('verify-persisted-events-and-drafts', self.verify_storage),
            ]:
                self.check_step(name, action)
        except Exception as error:
            failure = self.redact(type(error).__name__ + ': ' + str(error))
            self.event('failure_trace', text=self.redact(traceback.format_exc()))
        finally:
            if self.session:
                try:
                    self.session.close()
                except Exception as error:
                    self.event('cleanup_error', error=self.redact(error))
            report = {
                'schema_version': 1, 'application_version': self.version,
                'timestamp_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'platform': platform.system() + ' ' + platform.release() + ' ' + platform.machine(),
                'python': platform.python_version(), 'backend': ', '.join(sorted(set(self.backends))) or 'not_started',
                'passed': failure is None, 'steps': self.steps, 'failure': failure,
                'process_exit_codes': self.exit_codes, 'captured_characters': self.capture_chars,
                'screen_parser_note': 'Orphaned pyte wide-character continuation cells are rendered as blanks; raw VT events are retained.',
                'test_dependencies': {name: importlib.metadata.version(name) for name in
                                      (['pywinpty', 'pyte', 'wcwidth'] if sys.platform == 'win32' else ['pexpect', 'pyte', 'wcwidth'])},
                'network_audio_and_background_reminders': 'disabled; no update or audio request is part of this scenario',
                'personal_learning_data_used': False,
                'limits': ['Screens are reconstructed from actual PTY/ConPTY output, not native window screenshots.',
                           'Host font, DPI, IME, physical keyboard shortcuts, and speaker output require separate manual acceptance.',
                           'Windows CI reports its actual server OS; it does not certify every Windows 11 terminal host.'],
            }
            self.output.mkdir(parents=True, exist_ok=True)
            (self.output / 'terminal-events.jsonl').write_text(
                ''.join(json.dumps(event, ensure_ascii=False) + '\n' for event in self.events), encoding='utf-8')
            write_report(self.output, report, self.frames)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        parser.error('Output directory must be absent or empty; existing evidence is preserved')
    temporary = tempfile.TemporaryDirectory(prefix='ielts-terminal-session-')
    scenario = Scenario(output, Path(temporary.name))
    report = scenario.execute()
    try:
        temporary.cleanup()
    except OSError as error:
        report['passed'] = False
        report['failure'] = 'Fixture cleanup failed: ' + scenario.redact(error)
        report['steps'].append({'name': 'fixture-cleanup', 'status': 'failed', 'details': report['failure']})
        write_report(output, report, scenario.frames)
    print(json.dumps({'passed': report['passed'], 'platform': report['platform'],
                      'backend': report['backend'], 'steps': len(report['steps']),
                      'failure': report['failure']}, ensure_ascii=False))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
