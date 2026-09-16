"""Tests for the portable IELTS CLI compatibility API.

The POSIX test uses two real subprocesses and a real temporary lock file.
Windows behavior is exercised through a deterministic ``msvcrt`` mock because
this test host is POSIX; the Windows branch is therefore not a Windows runtime
validation.
"""

import importlib
import os
import subprocess
import sys
import tempfile
import textwrap
import types
import unittest


HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import portable_compat


class PortableCompatTests(unittest.TestCase):
    def test_posix_lock_is_mutually_exclusive_across_processes(self):
        if os.name == "nt":
            self.skipTest("POSIX subprocess lock test")
        with tempfile.NamedTemporaryFile() as lock_file:
            script = textwrap.dedent(
                """
                import sys
                sys.path.insert(0, sys.argv[2])
                from portable_compat import file_locks as fcntl
                stream = open(sys.argv[1], 'a+b')
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                print('locked', flush=True)
                sys.stdin.read(1)
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                stream.close()
                """
            )
            proc = subprocess.Popen(
                [sys.executable, "-c", script, lock_file.name, HERE],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
            )
            try:
                self.assertEqual(proc.stdout.readline().strip(), "locked")
                with open(lock_file.name, "a+b") as contender:
                    with self.assertRaises(BlockingIOError):
                        portable_compat.file_locks.flock(
                            contender.fileno(),
                            portable_compat.file_locks.LOCK_EX
                            | portable_compat.file_locks.LOCK_NB,
                        )
                proc.stdin.write("x")
                proc.stdin.close()
                self.assertEqual(proc.wait(timeout=5), 0)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
                proc.stdout.close()

    def test_windows_branch_with_msvcrt_mock(self):
        calls = []
        fail_next = [False]

        def mock_locking(fd, mode, size):
            calls.append((fd, mode, size))
            if fail_next[0]:
                fail_next[0] = False
                raise OSError(13, "lock unavailable")

        fake_msvcrt = types.SimpleNamespace(
            LK_LOCK=1, LK_NBLCK=2, LK_UNLCK=3,
            locking=mock_locking,
        )
        original_name = os.name
        sys.modules["msvcrt"] = fake_msvcrt
        try:
            os.name = "nt"
            win = importlib.reload(portable_compat)
            with tempfile.TemporaryFile() as stream:
                win.file_locks.flock(stream.fileno(), win.file_locks.LOCK_EX | win.file_locks.LOCK_NB)
                self.assertGreaterEqual(os.fstat(stream.fileno()).st_size, 1)
                win.file_locks.flock(stream.fileno(), win.file_locks.LOCK_UN)
            self.assertEqual([item[1:] for item in calls], [(fake_msvcrt.LK_NBLCK, 1), (fake_msvcrt.LK_UNLCK, 1)])
            self.assertEqual(win.shutdown_signals(), (win.signal.SIGTERM,))
            self.assertIsNone(win.private_file(0))

            # A failed acquire followed by reminders.py's unconditional
            # finally/LOCK_UN must not call msvcrt unlock or mask the quiet
            # return.
            calls[:] = []
            fail_next[0] = True
            with tempfile.TemporaryFile() as failed:
                with self.assertRaises(BlockingIOError):
                    win.file_locks.flock(
                        failed.fileno(), win.file_locks.LOCK_EX | win.file_locks.LOCK_NB
                    )
                win.file_locks.flock(failed.fileno(), win.file_locks.LOCK_UN)
            self.assertEqual([item[1] for item in calls], [fake_msvcrt.LK_NBLCK])
        finally:
            os.name = original_name
            sys.modules.pop("msvcrt", None)
            importlib.reload(portable_compat)

    def test_posix_api_matches_expected_signals_and_permissions(self):
        if os.name == "nt":
            self.skipTest("POSIX API test")
        self.assertEqual(portable_compat.shutdown_signals(), (portable_compat.signal.SIGTERM, portable_compat.signal.SIGHUP))
        with tempfile.TemporaryFile() as stream:
            portable_compat.private_file(stream.fileno())
            self.assertEqual(os.fstat(stream.fileno()).st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
