#!/usr/bin/env python3
"""V3 每日任务与运行中提醒测试；不调用网络、播放器、notify-send 或 systemd。"""

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import curses
import pexpect

from audio_settings import AudioSettings
from ielts import TerminalStudy
from library import Catalog, StudyLibrary
from routine import Routine
from scheduler import StudyPlanner
from study import StateStore


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
CATALOG = Catalog(DATA / "catalog.json")
ENV = dict(os.environ, TERM="xterm-256color", LANG="C.UTF-8", LC_ALL="C.UTF-8")


class FakeClock:
    def __init__(self, value=0.0):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


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


class DailyTuiTests(unittest.TestCase):
    def make_app(self, directory, clock=None):
        store = StateStore(directory)
        store.__enter__()
        library = StudyLibrary(CATALOG, store)
        planner = StudyPlanner(directory, clock or FakeClock())
        planner.seed_from_library(library.profiles)
        routine = Routine(directory, clock=clock or FakeClock())
        app = TerminalStudy(session=library.session, store=store, audio_settings=AudioSettings(directory), pronouncer=FakePronouncer(), library=library, planner=planner, routine=routine)
        self.addCleanup(planner.close)
        self.addCleanup(store.__exit__, None, None, None)
        return app, library, planner, routine

    def test_daily_prioritizes_due_and_respects_batch_and_remaining_goals(self):
        with tempfile.TemporaryDirectory() as directory:
            app, library, planner, routine = self.make_app(directory)
            routine.update(new_goal=1, review_goal=2, batch_size=2)
            planner.seed_from_library({"ielts": {"records": {"cancel": {}, "coincide": {}, "competent": {}}}})
            app.start_daily_group()
            self.assertIsNotNone(app.round)
            self.assertEqual(app.round.context, "review")
            self.assertLessEqual(app.round.initial_count, 2)
            self.assertTrue(app.round.daily)

    def test_daily_new_group_skips_planner_seen_words_and_returns_panel_when_done(self):
        with tempfile.TemporaryDirectory() as directory:
            app, library, planner, routine = self.make_app(directory)
            planner.note("ielts", "cancel", "copy", True, event_id="seen")
            routine.update(new_goal=2, review_goal=0, batch_size=2)
            app.start_daily_group()
            self.assertIsNotNone(app.round)
            refs = [app.round.current] + app.round.queue
            self.assertTrue(refs)
            self.assertTrue(all(ref["word"] != "cancel" for ref in refs))
            for _ in range(60):
                if not app.round:
                    break
                if app.round.phase == "answer":
                    question = app.round.question
                    app.round.set_draft(str(question['correct_indices'][0] + 1) if question.get('options') else question['answer'])
                app.handle_round("\n")
                if app.round and app.round.phase == "done":
                    app.handle_round("\n")
            self.assertTrue(app.daily_panel)
            self.assertIsNone(app.round)

    def test_completed_goals_do_not_open_a_new_group(self):
        with tempfile.TemporaryDirectory() as directory:
            app, library, planner, routine = self.make_app(directory)
            routine.update(new_goal=0, review_goal=0, batch_size=5)
            app.start_daily_group()
            self.assertIsNone(app.round)
            self.assertTrue(app.daily_panel)

    def test_due_tick_only_sets_pending_and_snooze_or_zero_controls_it(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = FakeClock()
            app, library, planner, routine = self.make_app(directory, clock)
            planner.seed_from_library({"ielts": {"records": {"cancel": {}}}})
            app.session.set_draft("keep")
            clock.advance(30 * 60)
            app.poll_routine()
            self.assertTrue(app.reminder_pending)
            self.assertEqual(app.session.state["draft"], "keep")
            app.snooze_reminder()
            self.assertFalse(app.reminder_pending)
            routine.update(reminder_minutes=0)
            clock.advance(3600)
            app.poll_routine()
            self.assertFalse(app.reminder_pending)
            routine.update(reminder_minutes=30)
            self.assertFalse(routine.tick(True))

    def test_daily_review_groups_exclude_today_completed_due_cards(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = FakeClock(1700000000.0)
            app, library, planner, routine = self.make_app(directory, clock)
            planner.seed_from_library({"ielts": {"records": {word: { } for word in ("cancel", "coincide", "competent", "adapt")}}})
            routine.update(new_goal=0, review_goal=4, batch_size=2)
            routine.update(extra_after_daily=False)
            app.practice_settings["review_mode"] = "en_to_zh"
            completed = []
            for _ in range(2):
                app.start_daily_group()
                self.assertIsNotNone(app.round)
                words = [app.round.current["word"]] + [item["word"] for item in app.round.queue]
                self.assertEqual(len(words), 2)
                completed.extend(words)
                while app.round:
                    if app.round.phase == "answer":
                        app.round.set_draft(str(app.round.question["correct_indices"][0] + 1))
                    app.handle_round("\n")
                    if app.round and app.round.phase == "done":
                        app.handle_round("\n")
            self.assertEqual(len(set(completed)), 4)
            self.assertEqual(planner.stats()["review_words"], 4)
            self.assertEqual(len(planner.due_cards()), 4)
            self.assertTrue(all(card["stage"] == 0 for card in planner.due_cards()))
            clock.advance(86400)
            app.start_daily_group()
            self.assertIsNotNone(app.round)
            self.assertEqual(app.round.initial_count, 2)

    def test_daily_switches_to_new_words_after_today_available_reviews_are_done(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = FakeClock(1700000000.0)
            app, library, planner, routine = self.make_app(directory, clock)
            planner.seed_from_library({"ielts": {"records": {"cancel": {}, "coincide": {}}}})
            routine.update(new_goal=1, review_goal=4, batch_size=2)
            app.practice_settings["review_mode"] = "en_to_zh"
            app.start_daily_group()
            self.assertIsNotNone(app.round)
            while app.round:
                if app.round.phase == "answer":
                    app.round.set_draft(str(app.round.question["correct_indices"][0] + 1))
                app.handle_round("\n")
                if app.round and app.round.phase == "done":
                    app.handle_round("\n")
            self.assertEqual(planner.stats()["review_words"], 2)
            app.start_daily_group()
            self.assertIsNotNone(app.round)
            self.assertEqual(app.round.context, "learn")
            self.assertFalse(planner.has_seen("ielts", app.round.current["word"]))

    def test_routine_write_failure_keeps_memory_config(self):
        with tempfile.TemporaryDirectory() as directory:
            routine = Routine(directory)
            old = routine.config
            with patch.object(routine, "_write", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    routine.update(new_goal=99)
            self.assertEqual(routine.config, old)

    def test_cli_configure_daily_flags_and_minimal_pty_menu(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([str(ROOT / "ielts.py"), "--data-dir", directory, "--no-import", "--configure", "--new-goal", "7", "--review-goal", "8", "--batch-size", "3", "--reminder-minutes", "0"], capture_output=True, text=True, check=True)
            config = json.loads(result.stdout)
            self.assertEqual({config[key] for key in ("new_goal", "review_goal", "batch_size", "reminder_minutes")}, {7, 8, 3, 0})
            child = pexpect.spawn(str(ROOT / "ielts.py"), ["--data-dir", directory, "--no-import", "--learn"], env=ENV, dimensions=(6, 80), encoding="utf-8", timeout=4)
            try:
                child.expect("雅思随手练")
                child.send("\x19")
                child.expect("今日任务")
                child.send("\x1b")
                child.expect("学习菜单")
                child.send("\x1b")
                child.send("\x0c")
                child.expect("学习计划与提醒")
                child.send("\x1b")
            finally:
                if child.isalive():
                    child.sendcontrol("q")
                    child.expect(pexpect.EOF)
                child.close(force=True)

    def test_notify_empty_or_cli_locked_is_quiet(self):
        with tempfile.TemporaryDirectory() as directory:
            empty = subprocess.run([str(ROOT / "ielts.py"), "--notify-review", "--data-dir", directory], capture_output=True, text=True, check=True)
            self.assertFalse(json.loads(empty.stdout)["notified"])
            store = StateStore(directory)
            with store:
                locked = subprocess.run([str(ROOT / "ielts.py"), "--notify-review", "--data-dir", directory], capture_output=True, text=True, check=True)
                self.assertEqual(json.loads(locked.stdout)["reason"], "cli_running")


if __name__ == "__main__":
    unittest.main()
