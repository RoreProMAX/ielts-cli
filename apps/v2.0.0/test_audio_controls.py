#!/usr/bin/env python3
"""读音控制的无网络集成测试：直接驱动 TerminalStudy 的按键处理。"""

import copy
import json
from pathlib import Path
import tempfile
import unittest

import curses

from audio_settings import AudioSettings
from ielts import TerminalStudy
from study import StateStore, StudySession, fresh_state


WORDS = [
    {"name": "cancel", "trans": ["取消"]},
    {"name": "explosive", "trans": ["爆炸的"]},
]


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


class AudioControlTests(unittest.TestCase):
    def make_app(self, directory, state=None):
        store = StateStore(directory)
        store.__enter__()
        session = StudySession(WORDS, state if state is not None else fresh_state())
        settings = AudioSettings(directory)
        fake = FakePronouncer()
        app = TerminalStudy(session, store, audio_settings=settings, pronouncer=fake)
        return app, store, fake

    @staticmethod
    def close_store(store):
        store.__exit__(None, None, None)

    def test_default_off_manual_controls_and_no_learning_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            app, store, fake = self.make_app(directory)
            try:
                before = copy.deepcopy(app.session.state)
                self.assertFalse(app.audio_settings.auto_pronounce)
                app.handle(curses.KEY_F5)
                app.handle("\x10")  # Ctrl+P
                self.assertEqual(fake.spoken, ["cancel", "cancel"])
                self.assertEqual(app.session.state, before)
                self.assertEqual(fake.cancelled, 0)

                app.handle(curses.KEY_F6)
                self.assertTrue(app.audio_settings.auto_pronounce)
                self.assertEqual(fake.spoken, ["cancel", "cancel", "cancel"])
                app.handle(curses.KEY_F5)
                self.assertEqual(fake.spoken[-1], "cancel")
                self.assertEqual(len(fake.spoken), 4)
            finally:
                self.close_store(store)

    def test_auto_next_once_and_typing_or_check_does_not_repeat(self):
        with tempfile.TemporaryDirectory() as directory:
            app, store, fake = self.make_app(directory)
            try:
                app.handle(curses.KEY_F6)
                self.assertEqual(fake.spoken, ["cancel"])
                app.handle(curses.KEY_F4)
                self.assertEqual(fake.spoken, ["cancel", "explosive"])
                self.assertEqual(fake.cancelled, 1)

                app.handle("x")
                self.assertEqual(fake.spoken, ["cancel", "explosive"])
                app.session.set_draft("explosive")
                app.handle("\n")
                self.assertEqual(fake.spoken, ["cancel", "explosive"])
            finally:
                self.close_store(store)

    def test_disabling_auto_cancels_and_next_does_not_speak(self):
        with tempfile.TemporaryDirectory() as directory:
            app, store, fake = self.make_app(directory)
            try:
                app.handle(curses.KEY_F6)
                app.handle(curses.KEY_F6)
                self.assertFalse(app.audio_settings.auto_pronounce)
                self.assertEqual(fake.cancelled, 1)
                self.assertEqual(fake.spoken, ["cancel"])
                app.handle(curses.KEY_F4)
                self.assertEqual(fake.spoken, ["cancel"])
                self.assertEqual(fake.cancelled, 2)
                app.handle(curses.KEY_F5)
                self.assertEqual(fake.spoken, ["cancel", "explosive"])
            finally:
                self.close_store(store)

    def test_settings_persist_and_invalid_file_is_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = AudioSettings(directory)
            self.assertFalse(settings.auto_pronounce)
            settings.set_auto(True)
            reopened = AudioSettings(directory)
            self.assertTrue(reopened.auto_pronounce)

            path = Path(directory) / "settings.json"
            original = path.read_bytes()
            path.write_text("{bad", encoding="utf-8")
            bad = path.read_bytes()
            with self.assertRaises((ValueError, json.JSONDecodeError)):
                AudioSettings(directory)
            self.assertEqual(path.read_bytes(), bad)

            path.write_text('{"version":1,"auto_pronounce":"yes"}', encoding="utf-8")
            invalid_type = path.read_bytes()
            with self.assertRaises(ValueError):
                AudioSettings(directory)
            self.assertEqual(path.read_bytes(), invalid_type)
            self.assertNotEqual(original, invalid_type)

    def test_empty_error_pool_never_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            state = fresh_state()
            state["filter"] = "errors"
            state["current"] = ""
            app, store, fake = self.make_app(directory, state)
            try:
                self.assertIsNone(app.session.current_word)
                app.handle(curses.KEY_F5)
                app.handle("\x10")
                app.handle(curses.KEY_F6)
                app.handle(curses.KEY_F4)
                self.assertEqual(fake.spoken, [])
            finally:
                self.close_store(store)


if __name__ == "__main__":
    unittest.main()
