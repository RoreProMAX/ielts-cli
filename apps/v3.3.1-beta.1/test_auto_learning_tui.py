#!/usr/bin/env python3
"""自动学习入口、恢复与菜单的隔离测试；不调用真实发声或网络。"""

import json
import os
from pathlib import Path
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


class AutoLearningTuiTests(unittest.TestCase):
    def make_app(self, directory, fake=None):
        store = StateStore(directory)
        store.__enter__()
        library = StudyLibrary(CATALOG, store)
        planner = StudyPlanner(directory)
        planner.seed_from_library(library.profiles)
        routine = Routine(directory)
        app = TerminalStudy(library.session, store, audio_settings=AudioSettings(directory),
                            pronouncer=fake or FakePronouncer(), library=library,
                            planner=planner, routine=routine)
        self.addCleanup(planner.close)
        self.addCleanup(store.__exit__, None, None, None)
        return app, library, planner, routine

    def test_legacy_settings_migrate_to_mixed_without_changing_legacy_session(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / "practice_settings.json"
            legacy = '{"version":1,"learn_mode":"copy","review_mode":"recall"}'
            settings.write_text(legacy, encoding="utf-8")
            app, library, planner, routine = self.make_app(directory)
            self.assertEqual(app.practice_settings, {"version": 2, "learn_mode": "mixed", "review_mode": "recall"})
            self.assertEqual(json.loads(settings.read_text(encoding="utf-8"))["version"], 2)
            self.assertEqual(json.loads(settings.read_text(encoding="utf-8"))["learn_mode"], "mixed")

    def test_old_session_draft_starts_original_copy_question_and_menu_round_trip_preserves_it(self):
        with tempfile.TemporaryDirectory() as directory:
            app, library, planner, routine = self.make_app(directory)
            library.session.set_draft("can")
            app.start_quiz("mixed", limit=1)
            self.assertEqual(app.round.question["mode"], "copy")
            self.assertEqual(app.round.draft, "can")
            app.handle("\x1b")
            app.handle("3")
            app.handle("\n")
            self.assertEqual(app.picker, "mode")
            app.handle("\x1b")
            saved = json.loads((Path(directory) / "learn_round-ielts.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["draft"], "can")
            self.assertEqual(saved["question"]["mode"], "copy")
            self.assertIsNotNone(app.round)

    def test_daily_completed_group_returns_to_daily_panel(self):
        with tempfile.TemporaryDirectory() as directory:
            app, library, planner, routine = self.make_app(directory)
            routine.update(new_goal=1, review_goal=0, batch_size=1)
            app.start_daily_group()
            self.assertTrue(app.round.daily)
            for _ in range(20):
                if not app.round:
                    break
                if app.round.phase == "answer":
                    question = app.round.question
                    answer = str(question["correct_indices"][0] + 1) if question.get("options") else question["answer"]
                    app.round.set_draft(answer)
                app.handle_round("\n")
                if app.round and app.round.phase == "done":
                    app.handle_round("\n")
            self.assertTrue(app.daily_panel)
            self.assertIsNone(app.round)

    def test_review_completion_returns_to_automatic_learning(self):
        with tempfile.TemporaryDirectory() as directory:
            app, library, planner, routine = self.make_app(directory)
            planner.seed_from_library({'ielts': {'records': {'cancel': {}}}})
            app.start_review(limit=1)
            self.assertEqual(app.round.context, 'review')
            app.round.set_draft('cancel')
            app.handle_round('\n')
            app.handle_round('\n')
            self.assertIsNotNone(app.round)
            self.assertEqual(app.round.context, 'learn')
            self.assertEqual(app.round.mode, 'mixed')

    def test_default_learning_pty_answers_round_file_and_reaches_next_group(self):
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
            child = pexpect.spawn("python3", ["-c", fake_runner, "--data-dir", directory, "--no-import", "--learn"],
                                  cwd=str(ROOT), env=PTY_ENV, dimensions=(12, 100), encoding="utf-8", timeout=5)
            modes = []
            processed = set()
            answered = set()
            continued = set()
            round_path = Path(directory) / "learn_round-ielts.json"
            try:
                child.expect("雅思随手练", timeout=5)
                deadline = time.time() + 12
                while time.time() < deadline and len(modes) < 40:
                    if not round_path.exists():
                        time.sleep(0.1)
                        continue
                    state = json.loads(round_path.read_text(encoding="utf-8"))
                    if not state.get("active"):
                        time.sleep(0.05)
                        continue
                    event_id = state["event_id"]
                    if event_id not in processed:
                        processed.add(event_id)
                        modes.append(state["question"]["mode"])
                    if state["phase"] == "done":
                        if event_id not in continued:
                            continued.add(event_id)
                            child.send("\n")
                    elif event_id not in answered:
                        answered.add(event_id)
                        question = state["question"]
                        answer = str(question["correct_indices"][0] + 1) if question.get("options") else question["answer"]
                        child.send(answer + "\n")
                    time.sleep(0.15)
            finally:
                if child.isalive():
                    child.sendcontrol("q")
                    child.expect(pexpect.EOF, timeout=5)
                child.close(force=True)
            self.assertTrue({"copy", "en_to_zh", "zh_to_en", "recall"}.issubset(set(modes)), modes)
            self.assertGreaterEqual(len(modes), 21, modes)

    def test_six_line_pty_escape_opens_menu_and_mode_picker(self):
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
            child = pexpect.spawn("python3", ["-c", fake_runner, "--data-dir", directory, "--no-import", "--learn"],
                                  cwd=str(ROOT), env=PTY_ENV, dimensions=(6, 80), encoding="utf-8", timeout=5)
            try:
                child.expect("雅思随手练")
                child.send("\x1b")
                child.expect("学习菜单")
                child.send("3\n")
                child.expect("题型选择")
            finally:
                if child.isalive():
                    child.sendcontrol("q")
                    child.expect(pexpect.EOF, timeout=5)
                child.close(force=True)


if __name__ == "__main__":
    unittest.main()
