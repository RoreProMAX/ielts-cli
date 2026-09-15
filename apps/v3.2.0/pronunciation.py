#!/usr/bin/env python3
"""IELTS CLI 的可取消英音朗读后端。"""

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import urllib.parse
import urllib.request


TIMEOUT = 3
MAX_AUDIO_BYTES = 2 * 1024 * 1024


class Pronouncer:
    def __init__(self, cache_dir, accent="uk"):
        if accent not in ("uk", "us"):
            raise ValueError("仅支持 uk 或 us 口音")
        self.cache_dir = Path(cache_dir).expanduser().resolve()
        self.accent = accent
        self._condition = threading.Condition()
        self._generation = 0
        self._pending = None
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._process = None
        self._message = None
        self._closed = False
        self._worker.start()

    def _set_message(self, generation, message):
        with self._condition:
            if generation == self._generation and not self._closed:
                self._message = message

    def _cache_path(self, word):
        key = (self.accent + "\0" + word).encode("utf-8")
        return self.cache_dir / (hashlib.sha256(key).hexdigest() + ".audio")

    @staticmethod
    def _audio_bytes(data, content_type=""):
        sample = data[:64].lstrip().lower()
        content_type = content_type.lower()
        if "text/html" in content_type or "application/json" in content_type or sample.startswith((b"<!doctype", b"<html", b"{", b"[")):
            return False
        if content_type.startswith("audio/"):
            return True
        return data.startswith((b"ID3", b"RIFF", b"OggS", b"fLaC")) or (len(data) > 2 and data[0] == 0xff and data[1] & 0xe0 == 0xe0)

    def _download(self, word, generation):
        target = self._cache_path(word)
        try:
            if target.is_file() and target.stat().st_size <= MAX_AUDIO_BYTES and self._audio_bytes(target.read_bytes()):
                return target
        except OSError:
            pass
        try:
            voice_type = 1 if self.accent == "uk" else 2
            url = "https://dict.youdao.com/dictvoice?audio=%s&type=%d" % (urllib.parse.quote(word, safe=""), voice_type)
            response = urllib.request.urlopen(url, timeout=TIMEOUT)
            try:
                content_type = response.headers.get("Content-Type", "") if getattr(response, "headers", None) else ""
                data = response.read(MAX_AUDIO_BYTES + 1)
            finally:
                response.close()
            if not data or len(data) > MAX_AUDIO_BYTES or not self._audio_bytes(data, content_type):
                raise ValueError("下载内容不是有效音频")
            with self._condition:
                if generation != self._generation or self._closed:
                    return None
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            fd, temporary_name = tempfile.mkstemp(prefix="." + target.name + ".tmp-", dir=str(self.cache_dir))
            os.close(fd)
            temporary = Path(temporary_name)
            try:
                with open(temporary, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
            finally:
                if temporary.exists():
                    temporary.unlink()
            return target
        except Exception:
            raise

    def _worker_loop(self):
        while True:
            with self._condition:
                while self._pending is None and not self._closed:
                    self._condition.wait()
                if self._closed:
                    return
                word, generation = self._pending
                self._pending = None
            self._run(word, generation)

    def _run(self, word, generation):
        try:
            with self._condition:
                if generation != self._generation or self._closed:
                    return
            if not shutil.which("ffplay"):
                self._set_message(generation, "未找到 ffplay，无法播放读音。")
                return
            path = self._download(word, generation)
            if path is None:
                return
            with self._condition:
                if generation != self._generation or self._closed:
                    return
            player = shutil.which("ffplay")
            if not player:
                self._set_message(generation, "未找到 ffplay，无法播放读音。")
                return
            process = subprocess.Popen([player, "-nodisp", "-autoexit", "-loglevel", "error", str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            with self._condition:
                stale = generation != self._generation or self._closed
                if not stale:
                    self._process = process
            if stale:
                self._finish_process(process)
                return
            self._set_message(generation, "正在播放英音。" if self.accent == 'uk' else "正在播放美音。")
            result = process.wait()
            with self._condition:
                if self._process is process:
                    self._process = None
            if result != 0:
                self._set_message(generation, "读音播放失败（播放器退出异常）。")
            else:
                self._set_message(generation, "读音播放完毕。")
        except Exception as exc:
            self._set_message(generation, "读音播放失败：%s。" % type(exc).__name__)

    def speak(self, word):
        word = str(word)
        if not shutil.which("ffplay"):
            with self._condition:
                self._message = "未找到 ffplay，无法播放读音。"
            return
        with self._condition:
            if self._closed:
                return
            self._generation += 1
            generation = self._generation
            process = self._process
            self._message = None
            if process is not None:
                try:
                    process.terminate()
                except OSError:
                    pass
            self._pending = (word, generation)
            self._condition.notify()

    def cancel(self):
        with self._condition:
            self._generation += 1
            self._pending = None
            process = self._process
            self._message = None
            if process is not None:
                try:
                    process.terminate()
                except OSError:
                    pass

    @staticmethod
    def _finish_process(process):
        try:
            process.terminate()
        except OSError:
            pass
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=0.5)

    def close(self):
        with self._condition:
            self._closed = True
            self._generation += 1
            self._pending = None
            process = self._process
            if process is not None:
                try:
                    process.terminate()
                except OSError:
                    pass
            self._condition.notify_all()
        if process is not None:
            self._finish_process(process)
        self._worker.join(0.5)

    def poll_message(self):
        with self._condition:
            message = self._message
            self._message = None
            return message
