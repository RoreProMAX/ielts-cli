#!/usr/bin/env python3
"""真实 PTY 下的 curses CLI 集成测试；所有进度写入临时目录。"""

import json
import os
from pathlib import Path
import tempfile
import time
import unittest

import pexpect


ROOT = Path(__file__).resolve().parent
CLI = ROOT / "ielts.py"
ENV = dict(os.environ, TERM="xterm-256color", LANG="C.UTF-8", LC_ALL="C.UTF-8")
F2 = "\x1bOQ"
F3 = "\x1bOR"
F4 = "\x1bOS"


def active_profile(data_dir):
    payload = json.loads((Path(data_dir) / "library.json").read_text(encoding="utf-8"))
    return payload["dictionaries"][payload["active_dictionary"]]


class TuiTests(unittest.TestCase):
    def start(self, data_dir, size=(80, 8), ready=True):
        child = pexpect.spawn(
            str(CLI),
            ["--data-dir", str(data_dir), "--no-import", "--question-mode", "copy"],
            env=ENV,
            # pexpect.spawn expects (columns, rows) when setting the PTY.
            dimensions=(size[1], size[0]),
            encoding="utf-8",
            timeout=3,
        )
        try:
            child.expect("雅思随手练" if ready else "终端至少需要 40 列、6 行")
        except BaseException:
            child.close(force=True)
            raise
        return child

    @staticmethod
    def stop(child, key="\x11"):
        if child.isalive():
            child.send(key)
            child.expect(pexpect.EOF)
        child.close(force=True)

    def test_copy_enter_advances_once_and_q_is_printable(self):
        with tempfile.TemporaryDirectory() as directory:
            child = self.start(directory)
            try:
                child.send("q")
                time.sleep(0.15)
                self.assertTrue(child.isalive(), "普通单词字符 q 不应退出 TUI")
                child.sendcontrol("u")
                child.send("cancel")
                child.send("\r")
                child.expect("拼写正确")
                child.send("\r")
                child.expect("explosive")
            finally:
                self.stop(child)
            state = active_profile(directory)
            self.assertEqual(state["records"]["cancel"]["correct"], 1)
            self.assertEqual(state["records"]["cancel"]["mistakes"], 0)
            self.assertEqual(sum(state["days"].values()), 1)

    def test_restore_unfinished_draft_after_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            child = self.start(directory)
            child.send("can")
            self.stop(child)
            first = active_profile(directory)
            self.assertEqual(first["draft"], "can")

            reopened = self.start(directory)
            try:
                reopened.expect("can")
            finally:
                self.stop(reopened)

    def test_sizes_and_resize_recovery(self):
        for size in ((80, 8), (120, 6), (40, 6)):
            with self.subTest(size=size), tempfile.TemporaryDirectory() as directory:
                child = self.start(directory, size=size)
                self.stop(child)

        with tempfile.TemporaryDirectory() as directory:
            child = self.start(directory, size=(40, 5), ready=False)
            try:
                time.sleep(0.2)
                child.setwinsize(8, 80)
                child.expect('雅思随手练')
            finally:
                self.stop(child)

    def test_f2_f3_f4_and_tab_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            child = self.start(directory)
            try:
                child.send(F2)
                child.expect("错词复习")
                child.send(F2)
                child.expect('对照拼写')
                child.send("\t")
                child.send(F3)
                child.expect("cancel")
                child.send(F4)
                child.send(F3)
                child.expect("explosive")
            finally:
                self.stop(child)

    def test_ctrl_q_and_ctrl_c_restore_shell_termios_and_input(self):
        for signal_key in ("\x11", "\x03"):
            with self.subTest(signal=repr(signal_key)), tempfile.TemporaryDirectory() as directory:
                shell = pexpect.spawn("/bin/sh", env=ENV, dimensions=(8, 80), encoding="utf-8", timeout=3)
                command = (
                    "printf 'BEFORE:%%s\\n' \"$(stty -g)\"; "
                    "python3 %s --data-dir %s --no-import; "
                    "printf 'AFTER:%%s\\n' \"$(stty -g)\""
                    % (CLI, directory)
                )
                shell.sendline(command)
                shell.expect(r"BEFORE:([0-9a-f:]+)")
                before = shell.match.group(1)
                shell.expect("雅思随手练")
                shell.send(signal_key)
                shell.expect(r"AFTER:([0-9a-f:]+)")
                after = shell.match.group(1)
                self.assertEqual(before, after, "退出后 shell 的 termios 应恢复")
                shell.sendline("printf SHELL_INPUT_OK")
                shell.expect("SHELL_INPUT_OK")
                shell.sendline("exit")
                shell.expect(pexpect.EOF)
                shell.close(force=True)


if __name__ == "__main__":
    unittest.main()
