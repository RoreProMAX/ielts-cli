#!/usr/bin/env python3
"""真实 PTY 下的词库、章节选择器测试；进度始终写入临时目录。"""

import json
import os
from pathlib import Path
import tempfile
import unittest

import pexpect


ROOT = Path(__file__).resolve().parent
CLI = ROOT / "ielts.py"
ENV = dict(os.environ, TERM="xterm-256color", LANG="C.UTF-8", LC_ALL="C.UTF-8")
F7 = "\x1b[18~"
F8 = "\x1b[19~"
UP = "\x1bOA"
DOWN = "\x1bOB"
PAGE_UP = "\x1b[5~"
PAGE_DOWN = "\x1b[6~"
HOME = "\x1bOH"
END = "\x1bOF"


def library_payload(directory):
    return json.loads((Path(directory) / "library.json").read_text(encoding="utf-8"))


def active_profile(directory):
    payload = library_payload(directory)
    return payload["dictionaries"][payload["active_dictionary"]]


class PickerTuiTests(unittest.TestCase):
    def start(self, data_dir, size=(80, 8)):
        child = pexpect.spawn(
            str(CLI), ["--data-dir", str(data_dir), "--no-import"], env=ENV,
            dimensions=(size[1], size[0]), encoding="utf-8", timeout=3,
        )
        try:
            child.expect("雅思随手练")
        except BaseException:
            child.close(force=True)
            raise
        return child

    @staticmethod
    def stop(child):
        if child.isalive():
            child.sendcontrol("q")
            child.expect(pexpect.EOF)
        child.close(force=True)

    def test_dictionary_switch_and_return_preserves_draft(self):
        with tempfile.TemporaryDirectory() as directory:
            child = self.start(directory)
            try:
                child.send("prefix")
                child.send(F7)
                child.expect("词库选择")
                child.send("2\r")
                child.expect("英语四级")
                child.send(F7)
                child.expect("词库选择")
                child.send("1\r")
                child.expect("雅思通用")
            finally:
                self.stop(child)
            payload = library_payload(directory)
            self.assertEqual(payload["active_dictionary"], "ielts")
            self.assertEqual(payload["dictionaries"]["ielts"]["draft"], "prefix")
            self.assertEqual(payload["dictionaries"]["cet4"]["draft"], "")

    def test_minimum_terminal_picker_and_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            child = self.start(directory, size=(40, 6))
            try:
                child.send(F7)
                child.expect('词库选择')
                child.send(END + '\r')
                child.expect('雅思扩展')
                child.send(F8)
                child.expect('章节选择')
                child.send('470\r')
                child.send(F8)
                child.expect('章节选择')
                child.send('\x1b')
            finally:
                self.stop(child)
            payload = library_payload(directory)
            self.assertEqual(payload['active_dictionary'], 'ielts-expanded')
            words = json.loads((ROOT / 'data/dicts/ielts-expanded.json').read_text())
            valid = [word for word in words if isinstance(word.get('name'), str) and word['name']]
            self.assertEqual(active_profile(directory)['current'], valid[469 * 20]['name'])

    def test_chapter_two_starts_at_position_21(self):
        with tempfile.TemporaryDirectory() as directory:
            child = self.start(directory)
            try:
                child.send(F8)
                child.expect("章节选择")
                child.send("2\r")
                child.expect("章2/")
                child.expect("词1/20")
            finally:
                self.stop(child)
            self.assertEqual(active_profile(directory)["current"], "competent")

    def test_last_chapter_shows_actual_word_count(self):
        with tempfile.TemporaryDirectory() as directory:
            child = self.start(directory)
            try:
                child.send(F8)
                child.expect("章节选择")
                child.send("179\r")
                child.expect("章179/179")
                child.expect("词1/15")
            finally:
                self.stop(child)

    def test_out_of_range_number_does_not_change_learning_state(self):
        with tempfile.TemporaryDirectory() as directory:
            child = self.start(directory)
            try:
                child.send("draft")
                child.send(F8)
                child.expect("章节选择")
                child.send("999\r")
                child.expect("请输入 1–179")
                child.send("\x1b")
                child.expect("draft")
            finally:
                self.stop(child)
            profile = active_profile(directory)
            self.assertEqual(profile["current"], "cancel")
            self.assertEqual(profile["draft"], "draft")
            self.assertEqual(profile["records"], {})
            self.assertEqual(profile["days"], {})

    def test_escape_does_not_drop_draft_and_navigation_keys_are_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            child = self.start(directory, size=(120, 6))
            try:
                child.send("keep-me")
                child.send(F7)
                child.expect("词库选择")
                child.send(UP + DOWN + PAGE_UP + PAGE_DOWN + HOME + END)
                child.send("\x1b")
                child.expect("keep-me")
                child.send("\x04")
                child.expect("词库选择")
                child.send("\x1b")
                child.expect("keep-me")
            finally:
                self.stop(child)
            self.assertEqual(active_profile(directory)["draft"], "keep-me")

    def test_restart_restores_active_dictionary_chapter_and_draft(self):
        with tempfile.TemporaryDirectory() as directory:
            child = self.start(directory)
            try:
                child.send(F7)
                child.expect("词库选择")
                child.send("2\r")
                child.expect("英语四级")
                child.send(F8)
                child.expect("章节选择")
                child.send("2\r")
                child.expect("章2/")
                child.send("ca")
            finally:
                self.stop(child)

            reopened = self.start(directory)
            try:
                reopened.expect("英语四级")
                reopened.expect("章2/")
                reopened.expect("ca")
            finally:
                self.stop(reopened)
            payload = library_payload(directory)
            self.assertEqual(payload["active_dictionary"], "cet4")
            self.assertEqual(payload["dictionaries"]["cet4"]["draft"], "ca")


if __name__ == "__main__":
    unittest.main()
