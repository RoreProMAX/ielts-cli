#!/usr/bin/env python3
"""V1 复习轮次 PTY 集成测试；测试数据全部位于临时目录。"""

import json
import os
from pathlib import Path
import tempfile
import unittest

import pexpect


ROOT = Path(__file__).resolve().parent
CLI = ROOT / "ielts.py"
ENV = dict(os.environ, TERM="xterm-256color", LANG="C.UTF-8", LC_ALL="C.UTF-8")


def legacy_fixture(directory, draft="normal-draft"):
    state = {"version": 1, "mode": "recall", "filter": "all", "current": "cancel", "draft": draft, "revealed": False, "hadError": False, "phase": "typing", "records": {}, "days": {}}
    for word in ("cancel", "coincide", "competent"):
        state["records"][word] = {"correct": 1, "mistakes": 0, "recall": 1, "needsReview": False}
    path = Path(directory) / "progress.json"
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return path


class ReviewTuiTests(unittest.TestCase):
    def start(self, directory, *options):
        child = pexpect.spawn(str(CLI), ["--data-dir", str(directory), "--no-import", *options], env=ENV, dimensions=(8, 80), encoding="utf-8", timeout=4)
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

    def test_due_round_is_recall_only_and_wrong_answer_gets_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            legacy_fixture(directory, draft="normal-draft")
            child = self.start(directory)
            try:
                child.expect("复习")
                self.assertNotIn("cancel", child.before)
                child.send("wrong\r")
                child.expect("这次没答对")
                child.send("cancel\r")
                child.expect("补练完成")
                child.send("\r")
                self.assertTrue(child.isalive())
            finally:
                self.stop(child)
            self.assertTrue((Path(directory) / "learning.sqlite3").is_file())

    def test_f9_pauses_to_normal_and_preserves_normal_draft(self):
        with tempfile.TemporaryDirectory() as directory:
            legacy_fixture(directory)
            child = self.start(directory)
            try:
                child.expect("复习")
                child.sendcontrol("b")
                child.expect("复习已暂停")
            finally:
                self.stop(child)
            state = json.loads((Path(directory) / "library.json").read_text(encoding="utf-8"))
            active = state["dictionaries"][state["active_dictionary"]]
            self.assertEqual(active["draft"], "normal-draft")

    def test_round_draft_and_review_state_restore_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            legacy_fixture(directory)
            child = self.start(directory)
            try:
                child.expect("答案 >")
                child.send("partial")
            finally:
                self.stop(child)
            round_path = Path(directory) / "active_round.json"
            self.assertEqual(json.loads(round_path.read_text(encoding="utf-8"))["draft"], "partial")

            reopened = self.start(directory)
            try:
                reopened.expect("复习")
                reopened.expect("partial")
                self.assertNotIn("cancel", reopened.before)
            finally:
                self.stop(reopened)

    def test_learn_bypasses_due_review_and_audio_defaults_off(self):
        with tempfile.TemporaryDirectory() as directory:
            legacy_fixture(directory)
            child = self.start(directory, "--learn")
            try:
                child.expect("章1/")
                child.expect("音:关")
                self.assertNotIn("答案 >", child.before)
                self.assertFalse((Path(directory) / "active_round.json").exists())
            finally:
                self.stop(child)


if __name__ == "__main__":
    unittest.main()
