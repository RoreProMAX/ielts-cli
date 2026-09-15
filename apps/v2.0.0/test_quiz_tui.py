#!/usr/bin/env python3
"""V2 题型与 TUI 状态集成测试；不使用真实用户目录、网络或播放器。"""

import copy
import json
import os
from pathlib import Path
import tempfile
import unittest

import curses
import pexpect

from audio_settings import AudioSettings
from ielts import TerminalStudy
from library import Catalog, StudyLibrary
from scheduler import StudyPlanner
from study import StateStore


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
WORDS = json.loads((DATA / "ielts.json").read_text(encoding="utf-8"))
CATALOG = Catalog(DATA / "catalog.json")
PTY_ENV = dict(os.environ, TERM="xterm-256color", LANG="C.UTF-8", LC_ALL="C.UTF-8")


class FakePronouncer:
    def __init__(self):
        self.spoken = []
        self.cancelled = 0

    def speak(self, word):
        self.spoken.append(word)

    def cancel(self):
        self.cancelled += 1

    def poll_message(self):
        return None

    def close(self):
        pass


class QuizTuiTests(unittest.TestCase):
    def make_app(self, directory, library, planner, fake=None):
        session = library.session
        return TerminalStudy(
            session,
            StateStore(directory),
            audio_settings=AudioSettings(directory),
            pronouncer=fake or FakePronouncer(),
            library=library,
            planner=planner,
        )

    def test_f10_and_ctrl_o_open_and_close_question_picker_in_real_pty(self):
        with tempfile.TemporaryDirectory() as directory:
            child = pexpect.spawn(
                str(ROOT / "ielts.py"),
                ["--data-dir", directory, "--no-import", "--learn"],
                env=PTY_ENV,
                dimensions=(12, 80),
                encoding="utf-8",
                timeout=4,
            )
            try:
                child.expect("雅思随手练")
                child.send("\x1b[21~")  # xterm F10
                child.expect("题型选择")
                child.send("\x1b")
                child.expect("雅思随手练")
                child.sendcontrol("o")
                child.expect("题型选择")
            finally:
                if child.isalive():
                    child.sendcontrol("q")
                    child.expect(pexpect.EOF)
                child.close(force=True)

    def test_question_mode_flags_start_each_mode_in_pty_with_fake_pronouncer(self):
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
        labels = {
            "en_to_zh": "英选中",
            "zh_to_en": "中选英",
            "listening": "听音拼写",
            "cloze": "例句填空",
            "collocation": "常用搭配",
            "mixed": "跟打",
        }
        for mode, label in labels.items():
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                child = pexpect.spawn(
                    os.environ.get("PYTHON", "python3"),
                    ["-c", fake_runner, "--data-dir", directory, "--no-import", "--learn", "--question-mode", mode],
                    cwd=str(ROOT),
                    env=PTY_ENV,
                    dimensions=(12, 100),
                    encoding="utf-8",
                    timeout=4,
                )
                try:
                    child.expect(label)
                finally:
                    if child.isalive():
                        child.sendcontrol("q")
                        child.expect(pexpect.EOF)
                    child.close(force=True)
                settings = json.loads((Path(directory) / "practice_settings.json").read_text(encoding="utf-8"))
                self.assertEqual(settings["learn_mode"], mode)

    def test_all_question_modes_have_expected_answers_and_content(self):
        with tempfile.TemporaryDirectory() as directory:
            with StateStore(directory) as store:
                library = StudyLibrary(CATALOG, store)
                planner = StudyPlanner(directory)
                fake = FakePronouncer()
                app = self.make_app(directory, library, planner, fake)
                try:
                    for mode in ("en_to_zh", "zh_to_en", "listening", "cloze", "collocation"):
                        question = app.make_question("ielts", "adapt", mode)
                        self.assertEqual(question["word"], "adapt")
                        if mode in ("en_to_zh", "zh_to_en"):
                            self.assertEqual(len(question["options"]), 4)
                            self.assertEqual(len(question["correct_indices"]), 1)
                        else:
                            self.assertEqual(question["answer"].casefold(), "adapt")
                        if mode == "cloze":
                            self.assertEqual(question["prompt"].count("____"), 1)
                        if mode == "collocation":
                            self.assertEqual(question["prompt"], "____ to change")
                finally:
                    planner.close()

    def test_learning_question_grades_only_its_mode_and_advances_bookmark(self):
        with tempfile.TemporaryDirectory() as directory:
            with StateStore(directory) as store:
                library = StudyLibrary(CATALOG, store)
                planner = StudyPlanner(directory)
                fake = FakePronouncer()
                app = self.make_app(directory, library, planner, fake)
                try:
                    app.start_quiz("en_to_zh", limit=1, resume=False)
                    self.assertIsNotNone(app.round)
                    question = app.round.question
                    correct = question["correct_indices"][0] + 1
                    app.handle_round(str(correct))
                    self.assertEqual(app.round.phase, "done")
                    self.assertEqual(planner.mode_stats("ielts", "adapt")["en_to_zh"]["correct"], 0)
                    self.assertEqual(planner.mode_stats("ielts", "cancel")["en_to_zh"]["correct"], 1)
                    self.assertEqual(planner.mode_stats("ielts", "cancel")["recall"]["correct"], 0)
                    app.handle_round("\n")
                    self.assertIsNone(app.round)
                    self.assertEqual(library.session.state["current"], "explosive")
                    card = planner.db.execute("SELECT stage FROM cards WHERE dictionary_id=? AND word=?", ("ielts", "cancel")).fetchone()
                    self.assertIsNotNone(card)
                    self.assertEqual(card[0], 0)
                finally:
                    planner.close()

    def test_mixed_intro_updates_typing_progress_without_advancing_mastery(self):
        with tempfile.TemporaryDirectory() as directory, StateStore(directory) as store:
            library = StudyLibrary(CATALOG, store)
            planner = StudyPlanner(directory)
            app = self.make_app(directory, library, planner)
            try:
                app.start_quiz('mixed', limit=5, resume=False)
                self.assertEqual(app.round.question['mode'], 'copy')
                app.round.set_draft('cancel')
                app.handle_round('\n')
                self.assertEqual(library.session.state['records']['cancel']['correct'], 1)
                self.assertEqual(library.session.state['records']['cancel']['recall'], 0)
                self.assertEqual(planner.db.execute('SELECT stage FROM cards WHERE word=?', ('cancel',)).fetchone()[0], 0)
                app.handle_round('\n')
                for _ in range(3):
                    app.handle_round(curses.KEY_F4)
                self.assertEqual(app.round.current['word'], 'cancel')
                self.assertEqual(app.round.question['mode'], 'en_to_zh')
            finally:
                planner.close()

    def test_question_mode_change_keeps_current_draft_until_next_question(self):
        with tempfile.TemporaryDirectory() as directory:
            with StateStore(directory) as store:
                library = StudyLibrary(CATALOG, store)
                planner = StudyPlanner(directory)
                app = self.make_app(directory, library, planner)
                try:
                    app.start_quiz("en_to_zh", limit=2, resume=False)
                    old_question = copy.deepcopy(app.round.question)
                    app.round.set_draft("2")
                    app.picker_scope = "learn_mode"
                    app.apply_question_mode("cloze")
                    self.assertEqual(app.round.question, old_question)
                    self.assertEqual(app.round.draft, "2")
                    self.assertEqual(app.round.mode, "cloze")
                    self.assertEqual(json.loads(app.practice_settings_path.read_text())["learn_mode"], "cloze")
                finally:
                    planner.close()

    def test_audio_timing_for_listening_manual_hint_and_non_audio_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            with StateStore(directory) as store:
                library = StudyLibrary(CATALOG, store)
                planner = StudyPlanner(directory)
                fake = FakePronouncer()
                app = self.make_app(directory, library, planner, fake)
                try:
                    app.start_quiz("listening", limit=1, resume=False)
                    self.assertEqual(fake.spoken, ["cancel"])
                    self.assertEqual(app.round.question["answer"], "cancel")
                    self.assertEqual(app.round.word.get("ukphone"), "'kænsl")
                    app.handle("\x10")  # F5: manual hint/play path
                    self.assertEqual(fake.spoken, ["cancel", "cancel"])

                    app.pause_review()
                    app.start_quiz("zh_to_en", limit=1, resume=False)
                    self.assertEqual(fake.spoken, ["cancel", "cancel"])
                    self.assertEqual(app.round.question["prompt"], "取消")
                    self.assertEqual(app.round.question["answer"], "cancel")
                    self.assertIn("cancel", app.round.question["options"])
                    app.pause_review()
                    app.start_quiz("recall", limit=1, resume=False)
                    self.assertEqual(fake.spoken, ["cancel", "cancel"])
                finally:
                    planner.close()

    def test_each_book_gets_its_own_learning_round_and_reopens(self):
        with tempfile.TemporaryDirectory() as directory:
            with StateStore(directory) as store:
                library = StudyLibrary(CATALOG, store)
                planner = StudyPlanner(directory)
                app = self.make_app(directory, library, planner)
                try:
                    app.start_quiz("en_to_zh", limit=1, resume=False)
                    ielts_round = Path(directory) / "learn_round-ielts.json"
                    self.assertTrue(ielts_round.is_file())
                    app.pause_review()
                    library.switch_dictionary("cet4")
                    app.session = library.session
                    app.start_quiz("en_to_zh", limit=1, resume=False)
                    cet4_round = Path(directory) / "learn_round-cet4.json"
                    self.assertTrue(cet4_round.is_file())
                    self.assertNotEqual(ielts_round.read_bytes(), cet4_round.read_bytes())
                    app.pause_review()
                    library.save()
                finally:
                    planner.close()

            with StateStore(directory) as store:
                reopened_library = StudyLibrary(CATALOG, store)
                reopened_planner = StudyPlanner(directory)
                reopened = self.make_app(directory, reopened_library, reopened_planner)
                try:
                    self.assertEqual(reopened_library.active_id, "cet4")
                    restored = reopened.load_round("learn")
                    self.assertIsNotNone(restored)
                    self.assertEqual(restored.current["dictionary_id"], "cet4")
                    self.assertTrue((Path(directory) / "learn_round-ielts.json").is_file())
                finally:
                    reopened_planner.close()


if __name__ == "__main__":
    unittest.main()
