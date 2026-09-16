#!/usr/bin/env python3
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from study import StateStore, StudySession, fresh_state, load_words


def words():
    return [{"name": "apple", "trans": ["苹果"]}, {"name": "brave", "trans": ["勇敢"]}, {"name": "calm", "trans": ["平静"]}]


class StudyTests(unittest.TestCase):
    def test_chapters_and_cross_chapter_errors(self):
        book = [{"name": "word-%02d" % index, "trans": [str(index)]} for index in range(45)]
        session = StudySession(book, chapter_size=20)
        self.assertEqual((session.chapter_index, session.chapter_count, session.chapter_position, session.chapter_word_count), (0, 3, 1, 20))
        self.assertEqual(session.chapter_info(2), {"index": 2, "start": 41, "end": 45, "total": 5, "practiced": 0, "first": "word-40", "last": "word-44"})
        session.select_chapter(2)
        self.assertEqual((session.current_word["name"], session.chapter_index, session.chapter_position), ("word-40", 2, 1))
        session.state["records"]["word-01"] = {"correct": 2, "mistakes": 0, "recall": 0, "needsReview": True}
        session.state["records"]["word-42"] = {"correct": 1, "mistakes": 0, "recall": 0, "needsReview": True}
        self.assertEqual(session.error_count, 2)
        self.assertEqual(session.chapter_info(0)["practiced"], 1)
        session.state["current"] = "word-19"
        session.next_word()
        self.assertEqual((session.current_word["name"], session.chapter_index), ("word-20", 1))
        session.state["current"] = "word-44"
        session.next_word()
        self.assertEqual((session.current_word["name"], session.chapter_index), ("word-00", 0))
        for bad in (True, -1, 3, "1"):
            with self.assertRaises(ValueError):
                session.select_chapter(bad)
        for bad in (True, 0, 501, "20"):
            with self.assertRaises(ValueError):
                StudySession(book, chapter_size=bad)

    def test_old_state_compatible_with_chapters(self):
        state = fresh_state()
        state["current"] = "apple"
        session = StudySession(words(), state=state, chapter_size=2)
        self.assertEqual(session.chapter_info(0)["total"], 2)

    def test_modes_errors_repeated_submit_and_clear(self):
        session = StudySession(words())
        self.assertEqual(session.position, 1)
        session.set_draft("wrong")
        session.submit()
        self.assertEqual(session.error_count, 1)
        session.set_draft("apple")
        session.submit()
        self.assertEqual(session.state["phase"], "success")
        session.submit()
        self.assertEqual(session.current_word["name"], "brave")
        session.toggle_errors()
        self.assertEqual(session.current_word["name"], "apple")
        session.toggle_mode()
        session.set_draft("wrong")
        session.submit()
        session.set_draft("apple")
        session.submit()
        self.assertEqual(session.error_count, 1)  # 曾提示/出错的默写不清除
        state = fresh_state()
        state["mode"] = "recall"
        state["filter"] = "errors"
        state["current"] = "apple"
        state["records"]["apple"] = {"correct": 0, "mistakes": 1, "recall": 0, "needsReview": True}
        clean = StudySession(words(), state)
        clean.set_draft("apple")
        clean.submit()
        self.assertEqual(clean.error_count, 0)  # 无提示无错误默写可清除
        self.assertEqual(session.today_count, 2)

    def test_state_store_restore_import_and_corrupt(self):
        state = fresh_state()
        state["current"] = "apple"
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "mcp.json"
            source.write_text(json.dumps({"version": 1, "revision": 3, "progress": state}), encoding="utf-8")
            cli_dir = Path(directory) / "cli"
            with StateStore(cli_dir, import_from=source) as store:
                self.assertEqual(store.load(), state)
                store.save(state)
            self.assertEqual(json.loads(source.read_text())["revision"], 3)
            self.assertEqual(StateStore(cli_dir).load(), state)
            (cli_dir / "progress.json").write_text("{bad", encoding="utf-8")
            with self.assertRaises(ValueError):
                StateStore(cli_dir).load()

    def test_raw_import_null_and_nonblocking_session_lock(self):
        state = fresh_state()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "raw.json"
            source.write_text(json.dumps(state), encoding="utf-8")
            cli_dir = Path(directory) / "cli"
            self.assertEqual(StateStore(cli_dir, import_from=source).load(), state)
            null_source = Path(directory) / "null.json"
            null_source.write_text(json.dumps({"version": 1, "revision": 1, "progress": None}), encoding="utf-8")
            null_cli = Path(directory) / "null-cli"
            self.assertEqual(StateStore(null_cli, import_from=null_source).load(), fresh_state())
            with StateStore(cli_dir):
                with self.assertRaises(RuntimeError):
                    with StateStore(cli_dir):
                        pass

    def test_parallel_store_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            script = """import sys; sys.path.insert(0, %r); from study import StateStore, fresh_state; s=fresh_state(); s['current']='x'; StateStore(%r).save(s)""" % (str(Path(__file__).parent), directory)
            # 先确认一个独立进程已持锁，再让另一个进程尝试；不依赖调度重叠。
            holder_script = """import sys; sys.path.insert(0, %r); from study import StateStore, fresh_state
with StateStore(%r) as store:
    print('LOCKED', flush=True)
    sys.stdin.readline()
    state = fresh_state(); state['current'] = 'x'; store.save(state)
""" % (str(Path(__file__).parent), directory)
            holder = subprocess.Popen([sys.executable, "-c", holder_script], stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertEqual(holder.stdout.readline().strip(), 'LOCKED')
                contender = subprocess.run([sys.executable, "-c", script], capture_output=True,
                                           text=True, timeout=5)
                self.assertEqual(contender.returncode, 1)
                self.assertIn('RuntimeError', contender.stderr)
                holder.communicate('\n', timeout=5)
                self.assertEqual(holder.returncode, 0)
            finally:
                if holder.poll() is None:
                    holder.kill()
                    holder.communicate(timeout=5)
            self.assertEqual(StateStore(directory).load()["current"], "x")

    def test_load_words(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "words.json"
            path.write_text(json.dumps(words() + [{"name": "apple", "trans": ["duplicate"]}, {"name": "bad", "trans": "x"}]), encoding="utf-8")
            self.assertEqual(len(load_words(path)), 3)


if __name__ == "__main__":
    unittest.main()
