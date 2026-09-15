#!/usr/bin/env python3
import tempfile
import subprocess
from pathlib import Path
import threading
import time
import unittest
from unittest import mock

from pronunciation import Pronouncer


class FakeResponse:
    def __init__(self, data=b"ID3audio"):
        self.data = data
        self.headers = {"Content-Type": "audio/mpeg"}

    def read(self, size=-1):
        return self.data if size < 0 else self.data[:size]

    def close(self):
        pass


class FakeProcess:
    def __init__(self, exit_code=0):
        self.exit_code = exit_code
        self.terminated = False
        self.waited = False
        self.done = threading.Event()

    def wait(self, timeout=None):
        self.waited = True
        if not self.done.wait(1 if timeout is None else timeout) and timeout is not None:
            raise subprocess.TimeoutExpired('fake-ffplay', timeout)
        return self.exit_code

    def terminate(self):
        self.terminated = True
        self.done.set()

    def kill(self):
        self.done.set()


class PronunciationTests(unittest.TestCase):
    def wait_for(self, predicate):
        for _ in range(300):
            if predicate():
                return
            time.sleep(0.01)
        self.fail("timed out")

    def test_cache_reuse_and_latest_cancel(self):
        with tempfile.TemporaryDirectory() as directory:
            processes = []
            downloads = []
            with mock.patch("pronunciation.shutil.which", return_value="/usr/bin/ffplay"), mock.patch("pronunciation.urllib.request.urlopen", side_effect=lambda url, timeout: downloads.append(url) or FakeResponse()), mock.patch("pronunciation.subprocess.Popen", side_effect=lambda *args, **kwargs: processes.append(FakeProcess()) or processes[-1]):
                pronouncer = Pronouncer(directory)
                pronouncer.speak("apple")
                self.wait_for(lambda: len(processes) == 1)
                pronouncer.speak("brave")
                self.wait_for(lambda: len(processes) == 2)
                self.assertTrue(processes[0].terminated)
                pronouncer.cancel()
                self.assertTrue(processes[1].terminated)
                pronouncer.speak("apple")
                self.wait_for(lambda: len(processes) == 3)
                self.assertEqual(len(downloads), 2)
                pronouncer.close()
            self.assertEqual(Path(directory).exists(), True)

    def test_playback_error_and_missing_player(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch("pronunciation.shutil.which", return_value=None), mock.patch("pronunciation.urllib.request.urlopen", return_value=FakeResponse()):
                pronouncer = Pronouncer(directory)
                pronouncer.speak("apple")
                message = []
                self.wait_for(lambda: (message.append(pronouncer.poll_message()) or message[-1] is not None))
                self.assertIn("未找到 ffplay", message[-1])
                pronouncer.close()
            with mock.patch("pronunciation.shutil.which", return_value="/usr/bin/ffplay"), mock.patch("pronunciation.urllib.request.urlopen", return_value=FakeResponse()), mock.patch("pronunciation.subprocess.Popen", side_effect=OSError("boom")):
                pronouncer = Pronouncer(directory)
                pronouncer.speak("apple")
                message = []
                self.wait_for(lambda: (message.append(pronouncer.poll_message()) or message[-1] is not None))
                self.assertIn("读音播放失败", message[-1])
                pronouncer.close()

    def test_nonzero_exit_and_close_reaps_process(self):
        with tempfile.TemporaryDirectory() as directory:
            process = FakeProcess(exit_code=7)
            with mock.patch("pronunciation.shutil.which", return_value="/usr/bin/ffplay"), mock.patch("pronunciation.urllib.request.urlopen", return_value=FakeResponse()), mock.patch("pronunciation.subprocess.Popen", return_value=process):
                pronouncer = Pronouncer(directory)
                pronouncer.speak("apple")
                message = []
                def failure_seen():
                    value = pronouncer.poll_message()
                    if value:
                        message.append(value)
                    return bool(message and "播放失败" in message[-1])
                self.wait_for(failure_seen)
                self.assertIn("播放失败", message[-1])
                pronouncer.close()
                self.assertTrue(process.waited)

    def test_blocked_old_download_only_plays_latest_word(self):
        with tempfile.TemporaryDirectory() as directory:
            started = threading.Event()
            release = threading.Event()
            downloads, played = [], []
            class SlowResponse(FakeResponse):
                def read(self, size=-1):
                    started.set()
                    if not release.wait(2):
                        raise TimeoutError('test download timed out')
                    return super().read(size)
            def download(url, timeout):
                downloads.append(url)
                return SlowResponse() if len(downloads) == 1 else FakeResponse()
            def play(command, **kwargs):
                played.append(command[-1])
                return FakeProcess()
            with mock.patch('pronunciation.shutil.which', return_value='/usr/bin/ffplay'), mock.patch('pronunciation.urllib.request.urlopen', side_effect=download), mock.patch('pronunciation.subprocess.Popen', side_effect=play):
                pronouncer = Pronouncer(directory)
                try:
                    pronouncer.speak('alpha')
                    self.assertTrue(started.wait(1))
                    worker = pronouncer._worker
                    pronouncer.speak('beta')
                    pronouncer.speak('gamma')
                    release.set()
                    self.wait_for(lambda: bool(played))
                    self.assertEqual(played, [str(pronouncer._cache_path('gamma'))])
                    self.assertEqual(len(downloads), 2)
                    self.assertIs(pronouncer._worker, worker)
                finally:
                    release.set()
                    pronouncer.close()

    def test_close_reaps_player_that_ignores_terminate(self):
        with tempfile.TemporaryDirectory() as directory:
            launched = threading.Event()
            process = FakeProcess()
            process.killed = False
            process.terminate = lambda: None
            def stubborn_wait(timeout=None):
                if timeout is not None and not process.done.is_set():
                    raise subprocess.TimeoutExpired('fake-ffplay', timeout)
                process.done.wait(2)
                return 0
            def kill():
                process.killed = True
                process.done.set()
            process.wait, process.kill = stubborn_wait, kill
            def play(*args, **kwargs):
                launched.set()
                return process
            with mock.patch('pronunciation.shutil.which', return_value='/usr/bin/ffplay'), mock.patch('pronunciation.urllib.request.urlopen', return_value=FakeResponse()), mock.patch('pronunciation.subprocess.Popen', side_effect=play):
                pronouncer = Pronouncer(directory)
                pronouncer.speak('alpha')
                self.wait_for(lambda: pronouncer._process is process)
                pronouncer.close()
                self.assertTrue(process.killed)
                self.assertFalse(pronouncer._worker.is_alive())


if __name__ == "__main__":
    unittest.main()
