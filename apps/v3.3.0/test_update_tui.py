"""更新入口与学习会话集成回归；使用临时数据和假网络/发声器。"""

import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ielts
from audio_settings import AudioSettings
from library import Catalog, StudyLibrary
from routine import Routine
from scheduler import StudyPlanner
from study import StateStore


ROOT = Path(__file__).resolve().parent
CATALOG = Catalog(ROOT / 'data/catalog.json')


class FakePronouncer:
    def __init__(self, *args, **kwargs):
        pass

    def speak(self, word):
        pass

    def cancel(self):
        pass

    def poll_message(self):
        return None

    def close(self):
        pass


class FakeUpdater:
    def __init__(self, data_dir, *args, **kwargs):
        self.data_dir = Path(data_dir)
        self.auto_check = True
        self.status = {'state': 'idle', 'version': '3.3.0', 'message': '尚未检查更新'}
        self.checks = self.installs = self.activations = self.closes = self.active_reads = 0
        self.activated = False
        self.activation_fails = False
        self.launcher = self.data_dir / 'updates/releases/3.4.0/launcher.py'

    def poll(self):
        return dict(self.status)

    def set_auto_check(self, enabled):
        self.auto_check = enabled

    def check_async(self):
        self.checks += 1
        return True

    def install_async(self):
        self.installs += 1
        self.status = {'state': 'downloading', 'version': '3.4.0', 'message': '正在下载更新'}
        return True

    def activate_ready(self):
        self.activations += 1
        if self.activation_fails:
            self.status = {'state': 'error', 'version': '3.4.0', 'message': '更新备份失败'}
            return None
        self.saved_before_activation = json.loads((self.data_dir / 'learn_round-ielts.json').read_text())
        self.activated = True
        return self.launcher

    def active_launcher(self):
        self.active_reads += 1
        return self.launcher if self.activated else None

    def close(self):
        self.closes += 1


class UpdateTuiTests(unittest.TestCase):
    @contextlib.contextmanager
    def make_app(self, directory):
        with StateStore(directory) as store:
            library = StudyLibrary(CATALOG, store)
            planner = StudyPlanner(directory)
            try:
                routine = Routine(directory)
                updater = FakeUpdater(directory)
                app = ielts.TerminalStudy(library.session, store, library=library, planner=planner,
                                          routine=routine, update_manager=updater,
                                          audio_settings=AudioSettings(directory), pronouncer=FakePronouncer())
                app.start_quiz('mixed', limit=1)
                app.round.set_draft('can')
                app.dirty = True
                yield app, updater
            finally:
                planner.close()

    def choose(self, app, value):
        index = next(i for i, row in enumerate(app.picker_rows) if row['value'] == value)
        app.handle(str(index + 1))
        app.handle('\n')

    def test_available_update_is_only_a_banner_and_keeps_current_question(self):
        with tempfile.TemporaryDirectory() as directory, self.make_app(directory) as (app, updater):
            before = app.round.snapshot()
            updater.status = {'state': 'available', 'version': '3.4.0', 'message': '发现稳定版 3.4.0'}
            app.poll_updates()
            self.assertEqual(app.round.snapshot(), before)
            self.assertIsNone(app.picker)
            self.assertTrue(app.running)
            self.assertIn('3.4.0', app.update_banner)
            self.assertEqual((updater.installs, updater.activations), (0, 0))
            app.open_update_picker()
            self.choose(app, 'continue')
            self.assertEqual(app.update_banner, '')
            self.assertEqual(app.round.snapshot(), before)

    def test_automatic_check_obeys_setting_bypass_and_test_environment(self):
        with tempfile.TemporaryDirectory() as directory, self.make_app(directory) as (app, updater), patch.dict(os.environ, {'IELTS_DISABLE_UPDATE_CHECK': '0'}):
            updater.auto_check = False
            self.assertFalse(app.start_update_check())
            updater.auto_check = True
            app.skip_update_check = True
            self.assertFalse(app.start_update_check())
            app.skip_update_check = False
            with patch.dict(os.environ, {'IELTS_DISABLE_UPDATE_CHECK': '1'}):
                self.assertFalse(app.start_update_check())
            self.assertTrue(app.start_update_check())
            self.assertEqual(updater.checks, 1)

    def test_menu_ten_and_learning_plan_toggle_keep_progress_and_allow_manual_check(self):
        with tempfile.TemporaryDirectory() as directory, self.make_app(directory) as (app, updater):
            before = app.round.snapshot()
            app.open_menu()
            app.handle('1')
            app.handle('0')
            app.handle('\n')
            self.assertEqual(app.picker, 'updates')
            self.choose(app, 'auto_check')
            self.assertFalse(updater.auto_check)
            self.choose(app, 'check_update')
            self.assertEqual(updater.checks, 1)
            self.assertEqual(updater.installs, 0)
            app.open_routine_picker()
            self.choose(app, 'auto_update_check')
            self.assertTrue(updater.auto_check)
            self.assertEqual(app.round.snapshot(), before)

    def test_download_and_activation_require_separate_choices_and_save_first(self):
        with tempfile.TemporaryDirectory() as directory, self.make_app(directory) as (app, updater):
            updater.status = {'state': 'available', 'version': '3.4.0', 'message': '发现新版'}
            app.open_update_picker()
            self.choose(app, 'install_update')
            self.assertEqual(updater.installs, 1)
            self.assertEqual(updater.activations, 0)
            self.assertTrue(app.running)
            updater.status = {'state': 'ready', 'version': '3.4.0', 'message': '校验完成'}
            app.poll_updates()
            self.assertEqual(updater.activations, 0)
            app.open_update_picker()
            self.choose(app, 'activate_update')
            self.assertEqual(updater.activations, 1)
            self.assertEqual(updater.saved_before_activation['draft'], 'can')
            self.assertTrue(app.running)
            self.assertIsNone(app.update_restart_path)
            app.open_update_picker()
            self.choose(app, 'restart_update')
            self.assertEqual(updater.activations, 1)
            self.assertFalse(app.running)
            self.assertEqual(app.update_restart_path, updater.launcher)
            self.assertEqual(app.update_restart_args, ['--learn'])

    def test_activation_failure_keeps_old_session_running(self):
        with tempfile.TemporaryDirectory() as directory, self.make_app(directory) as (app, updater):
            updater.status = {'state': 'ready', 'version': '3.4.0', 'message': '准备启用'}
            updater.activation_fails = True
            app.open_update_picker()
            self.choose(app, 'restart_update')
            self.assertTrue(app.running)
            self.assertFalse(app.update_activated)
            self.assertIsNone(app.update_restart_path)
            self.assertEqual(app.round.draft, 'can')
            self.assertEqual(app.picker_error, '更新备份失败')

    def test_no_update_version_and_status_are_read_only_and_do_not_check_network(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'not-created'
            updater = FakeUpdater(target)
            updater.activated = True
            with patch.object(ielts, 'UpdateManager', return_value=updater), patch.object(ielts, 'restart_with_update') as restart, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(ielts.main(['--no-update', '--version', '--data-dir', str(target)]), 0)
                self.assertEqual(ielts.main(['--no-update', '--update-status', '--data-dir', str(target)]), 0)
            restart.assert_not_called()
            self.assertEqual((updater.active_reads, updater.checks, updater.installs), (0, 0, 0))
            self.assertFalse(target.exists())

    def test_restart_happens_after_study_lock_is_released(self):
        with tempfile.TemporaryDirectory() as directory:
            updater = FakeUpdater(directory)
            updater.status = {'state': 'ready', 'version': '3.4.0', 'message': '已校验'}

            def interact(run):
                app = run.__self__
                app.start_quiz('mixed', limit=1)
                app.round.set_draft('can')
                app.dirty = True
                app.handle_update_choice('restart_update')

            def restart(launcher, data_dir, arguments):
                self.assertEqual(Path(launcher), updater.launcher)
                self.assertEqual(arguments, ['--learn'])
                self.assertEqual(Path(data_dir), Path(directory))
                with StateStore(directory):
                    state = json.loads((Path(directory) / 'learn_round-ielts.json').read_text())
                    self.assertEqual(state['draft'], 'can')

            with contextlib.redirect_stdout(io.StringIO()), patch.dict(os.environ, {'TERM': 'xterm-256color'}), patch.object(ielts, 'UpdateManager', return_value=updater), patch.object(ielts, 'Pronouncer', FakePronouncer), patch.object(ielts.curses, 'wrapper', side_effect=interact), patch.object(ielts.sys.stdin, 'isatty', return_value=True), patch.object(ielts.sys.stdout, 'isatty', return_value=True), patch.object(ielts, 'restart_with_update', side_effect=restart) as launch:
                self.assertEqual(ielts.main(['--data-dir', directory, '--no-import', '--no-update']), 0)
            self.assertEqual(launch.call_count, 1)


if __name__ == '__main__':
    unittest.main()
