#!/usr/bin/env python3
"""增量例句与搭配练习的 UI 集成测试；使用临时数据和假发声器。"""

import json
import curses
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

import pexpect

from audio_settings import AudioSettings
from ielts import TerminalStudy
from library import Catalog, StudyLibrary
from routine import Routine
from scheduler import StudyPlanner
from study import StateStore


ROOT = Path(__file__).resolve().parent
CATALOG = Catalog(ROOT / "data" / "catalog.json")
PTY_ENV = dict(os.environ, TERM="xterm-256color", LANG="C.UTF-8", LC_ALL="C.UTF-8")


class FakePronouncer:
    def __init__(self):
        self.spoken = []

    def speak(self, word):
        self.spoken.append(word)

    def cancel(self):
        pass

    def poll_message(self):
        return None

    def close(self):
        pass


class IncrementalTuiTests(unittest.TestCase):
    def make_app(self, directory, clock=lambda: 1700000000.0):
        store = StateStore(directory)
        store.__enter__()
        library = StudyLibrary(CATALOG, store)
        planner = StudyPlanner(directory, clock)
        planner.seed_from_library(library.profiles)
        routine = Routine(directory, clock=clock)
        app = TerminalStudy(library.session, store, audio_settings=AudioSettings(directory),
                            pronouncer=FakePronouncer(), library=library,
                            planner=planner, routine=routine)
        self.addCleanup(planner.close)
        self.addCleanup(store.__exit__, None, None, None)
        return app, library, planner, routine

    def seed_learned_cancel(self, planner):
        planner.note("ielts", "cancel", "copy", True, context="learn", event_id="seed-cancel")

    def finish_round(self, app, limit=10):
        modes = []
        for _ in range(limit):
            if not app.round:
                break
            if app.round.phase == "answer":
                question = app.round.question
                modes.append(question["mode"])
                answer = str(question["correct_indices"][0] + 1) if question.get("options") else question["answer"]
                app.round.set_draft(answer)
            app.handle_round("\n")
            if app.round and app.round.phase == "done":
                app.handle_round("\n")
        return modes

    def test_incremental_alternates_cloze_collocation_and_preserves_other_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            app, library, planner, routine = self.make_app(directory)
            self.seed_learned_cancel(planner)
            routine.update(new_goal=0, review_goal=0, batch_size=1)
            before = planner.stats()
            app.start_incremental(resume=True)
            self.assertEqual(app.round.context, "extra")
            self.assertEqual(app.round_path.name, "active_round.json")
            self.assertEqual(app.learn_round_path.name, "learn_round-ielts.json")
            modes = self.finish_round(app, limit=4)
            self.assertEqual(modes, ["cloze", "collocation"])
            self.assertTrue(app.daily_panel)
            self.assertFalse((Path(directory) / "learn_round-ielts.json").exists())
            self.assertFalse((Path(directory) / "active_round.json").exists())
            self.assertTrue((Path(directory) / "extra_round.json").exists())
            after = planner.stats()
            self.assertEqual((before["new_words"], before["review_words"]),
                             (after["new_words"], after["review_words"]))
            self.assertEqual(before["due"], after["due"])
            self.assertEqual(after.get("extra_attempts", 0), 2)

    def select_menu(self, app, number):
        app.handle('\x1b')
        app.handle(str(number))
        app.handle('\n')

    def test_return_to_learning_preserves_both_rounds_and_can_resume_extra(self):
        with tempfile.TemporaryDirectory() as directory:
            app, library, planner, routine = self.make_app(directory)
            self.seed_learned_cancel(planner)
            app.start_quiz('mixed', limit=1)
            app.round.set_draft('can')
            app.dirty = True
            app.persist()
            learning = app.round.snapshot()
            self.select_menu(app, 9)
            app.round.set_draft('c')
            app.dirty = True
            extra = app.round.snapshot()
            stats = planner.stats()
            self.select_menu(app, 2)
            self.assertEqual(app.round.context, 'learn')
            self.assertEqual(app.round.mode, 'mixed')
            for key in ('question', 'draft', 'event_id', 'current', 'queue'):
                self.assertEqual(app.round.snapshot()[key], learning[key])
            self.assertEqual(json.loads(app.extra_round_path.read_text()), extra)
            self.assertEqual(planner.stats(), stats)
            self.select_menu(app, 9)
            for key in ('question', 'draft', 'event_id', 'mode', 'context'):
                self.assertEqual(app.round.snapshot()[key], extra[key])

    def test_manual_word_mode_exits_extra_without_relabeling_its_bookmark(self):
        with tempfile.TemporaryDirectory() as directory:
            app, library, planner, routine = self.make_app(directory)
            self.seed_learned_cancel(planner)
            app.start_incremental()
            extra = app.round.snapshot()
            self.select_menu(app, 3)
            index = next(i for i, row in enumerate(app.picker_rows) if row['value'] == 'recall')
            app.handle(str(index + 1))
            app.handle('\n')
            self.assertEqual((app.round.context, app.round.mode), ('learn', 'recall'))
            self.assertEqual(json.loads(app.extra_round_path.read_text()), extra)

    def test_explicit_return_keeps_learning_after_goal_until_daily_is_requested(self):
        with tempfile.TemporaryDirectory() as directory:
            app, library, planner, routine = self.make_app(directory)
            self.seed_learned_cancel(planner)
            routine.update(new_goal=0, review_goal=0, batch_size=1)
            app.start_incremental()
            self.select_menu(app, 2)
            self.assertTrue(app.daily_work_complete())
            learning = app.round
            for _ in range(12):
                question = app.round.question
                app.round.set_draft(str(question['correct_indices'][0] + 1) if question.get('options') else question['answer'])
                app.handle_round('\n')
                app.handle_round('\n')
                if app.round is not learning:
                    break
            self.assertIsNot(app.round, learning)
            self.assertIsNotNone(app.round)
            self.assertEqual(app.round.context, 'learn')
            self.assertFalse(app.daily_panel)
            app.pause_review()
            app.start_daily_group()
            self.assertEqual(app.round.context, 'extra')

    def test_restore_extra_repairs_old_mode_pollution_without_changing_current_question(self):
        with tempfile.TemporaryDirectory() as directory:
            app, library, planner, routine = self.make_app(directory)
            self.seed_learned_cancel(planner)
            app.start_incremental()
            app.round.set_draft('ca')
            old = app.round.snapshot()
            old['mode'] = 'mixed'
            app.store._atomic_write(app.extra_round_path, old)
            app.round = None
            app.dirty = False
            app.start_incremental()
            self.assertEqual(app.round.mode, old['question']['mode'])
            for key in ('question', 'draft', 'event_id', 'phase', 'current', 'queue'):
                self.assertEqual(app.round.snapshot()[key], old[key])

    def test_daily_panel_review_key_saves_extra_and_starts_review(self):
        with tempfile.TemporaryDirectory() as directory:
            app, library, planner, routine = self.make_app(directory)
            planner.seed_from_library({'ielts': {'records': {'cancel': {}}}})
            app.start_incremental()
            app.round.set_draft('ca')
            app.dirty = True
            extra = app.round.snapshot()
            app.handle(curses.KEY_F11)
            app.handle(curses.KEY_F9)
            self.assertFalse(app.daily_panel)
            self.assertEqual(app.round.context, 'review')
            self.assertEqual(json.loads(app.extra_round_path.read_text()), extra)

    def test_incremental_gate_requires_new_and_review_work_and_can_be_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            app, library, planner, routine = self.make_app(directory)
            routine.update(new_goal=1, review_goal=1, batch_size=1)
            with patch.object(app, "planner_stats", return_value={"new_words": 0, "review_words": 0, "due": 1}), patch.object(planner, "due_cards", return_value=[{"word": "cancel"}]):
                self.assertFalse(app.daily_work_complete())
            with patch.object(app, "planner_stats", return_value={"new_words": 1, "review_words": 1, "due": 0}), patch.object(planner, "due_cards", return_value=[]):
                self.assertTrue(app.daily_work_complete())
            routine.update(extra_after_daily=False)
            with patch.object(app, "start_incremental") as start:
                self.assertFalse(app.maybe_start_incremental())
                start.assert_not_called()

    def test_routine_menu_toggles_both_settings_for_next_group(self):
        with tempfile.TemporaryDirectory() as directory:
            app, library, planner, routine = self.make_app(directory)
            app.open_menu()
            app.handle("8")
            app.handle("\n")
            self.assertEqual(app.picker, "routine")
            app.handle("7")
            app.handle("\n")
            app.handle("8")
            app.handle("\n")
            self.assertFalse(routine.config["extra_after_daily"])
            self.assertTrue(routine.config["examples_during_learning"])

    def test_less_due_than_goal_enters_extra_after_actual_review_is_done(self):
        with tempfile.TemporaryDirectory() as directory:
            app, library, planner, routine = self.make_app(directory)
            routine.update(new_goal=0, review_goal=30, batch_size=1)
            planner.seed_from_library({'ielts': {'records': {'cancel': {}}}})
            self.assertFalse(app.daily_work_complete())
            app.start_daily_group()
            self.assertEqual(app.round.context, 'review')
            app.round.set_draft('cancel')
            app.handle_round('\n')
            app.handle_round('\n')
            self.assertEqual(planner.stats()['review_words'], 1)
            self.assertTrue(app.daily_work_complete())
            self.assertEqual(app.round.context, 'extra')

    def test_extra_skip_does_not_repeat_forever_and_next_day_is_available(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = [1700000000.0]
            app, library, planner, routine = self.make_app(directory, clock=lambda: clock[0])
            self.seed_learned_cancel(planner)
            routine.update(batch_size=1)
            app.start_incremental()
            for _ in range(3):
                if not app.round:
                    break
                app.handle_round(curses.KEY_F4)
            self.assertIsNone(app.round)
            self.assertEqual(planner.stats()['extra_attempts'], 0)
            clock[0] += 86400
            app.start_incremental()
            self.assertIsNotNone(app.round)

    def test_examples_on_adds_both_types_without_replacing_current_group_setting(self):
        with tempfile.TemporaryDirectory() as directory:
            app, library, planner, routine = self.make_app(directory)
            routine.update(extra_after_daily=False, examples_during_learning=True, new_goal=1, review_goal=0, batch_size=1)
            app.start_daily_group()
            modes = self.finish_round(app, limit=12)
            self.assertEqual(modes, ['copy', 'en_to_zh', 'zh_to_en', 'recall', 'cloze', 'collocation'])
            self.assertEqual(planner.stats()['new_words'], 1)
            self.assertEqual(planner.stats()['extra_attempts'], 2)
            card = planner.db.execute('SELECT stage FROM cards WHERE dictionary_id=? AND word=?', ('ielts', 'cancel')).fetchone()
            self.assertEqual(card[0], 1)

    def test_configure_flags_and_incremental_cli_entry_with_seeded_learning(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([str(ROOT / "ielts.py"), "--data-dir", directory, "--no-import", "--configure", "--examples-during-learning", "on", "--extra-after-daily", "off"], capture_output=True, text=True, check=True)
            config = json.loads(result.stdout)
            self.assertTrue(config["examples_during_learning"])
            self.assertFalse(config["extra_after_daily"])
            app, library, planner, routine = self.make_app(directory)
            self.seed_learned_cancel(planner)
            planner.close()
            app.store.__exit__(None, None, None)
            fake_runner = """
import sys
import ielts
class FakePronouncer:
    def __init__(self, *args, **kwargs): pass
    def speak(self, word): pass
    def cancel(self): pass
    def poll_message(self): return None
    def close(self): pass
ielts.Pronouncer = FakePronouncer
raise SystemExit(ielts.main(sys.argv[1:]))
"""
            child = pexpect.spawn("python3", ["-c", fake_runner, "--data-dir", directory, "--no-import", "--incremental"], cwd=str(ROOT), env=PTY_ENV, dimensions=(10, 100), encoding="utf-8", timeout=5)
            try:
                child.expect("增量练习")
            finally:
                if child.isalive():
                    child.sendcontrol("q")
                    child.expect(pexpect.EOF, timeout=5)
                child.close(force=True)

    def test_extra_question_shows_example_translation_and_extra_context(self):
        with tempfile.TemporaryDirectory() as directory:
            app, library, planner, routine = self.make_app(directory)
            self.seed_learned_cancel(planner)
            routine.update(new_goal=0, review_goal=0, batch_size=1)
            app.start_incremental(resume=False)
            self.assertIn(app.round.question["mode"], ("cloze", "collocation"))
            self.assertIn("取消", app.example_text(app.round))
            self.finish_round(app, limit=2)
            row = planner.db.execute("SELECT context FROM events WHERE word=? AND context='extra' LIMIT 1", ("cancel",)).fetchone()
            self.assertEqual(row[0], "extra")

    def test_six_line_routine_picker_can_scroll_to_long_toggle_prompt(self):
        fake_runner = """
import sys
import ielts
class FakePronouncer:
    def __init__(self, *args, **kwargs): pass
    def speak(self, word): pass
    def cancel(self): pass
    def poll_message(self): return None
    def close(self): pass
ielts.Pronouncer = FakePronouncer
raise SystemExit(ielts.main(sys.argv[1:]))
"""
        with tempfile.TemporaryDirectory() as directory:
            child = pexpect.spawn("python3", ["-c", fake_runner, "--data-dir", directory, "--no-import", "--learn"], cwd=str(ROOT), env=PTY_ENV, dimensions=(6, 80), encoding="utf-8", timeout=5)
            child.delaybeforesend = 0
            try:
                child.expect("雅思随手练")
                child.send("\x1b")
                child.expect("学习菜单")
                child.send("8")
                child.send("\n")
                child.expect("提醒")
                # 6 行窗口中用编号跳到末项，验证分页后的长设置提示可见。
                child.send("8")
                child.expect("学习过程中加入例句")
            finally:
                if child.isalive():
                    child.sendcontrol("q")
                    child.expect(pexpect.EOF, timeout=5)
                child.close(force=True)


if __name__ == "__main__":
    unittest.main()
