#!/usr/bin/env python3
"""IELTS 命令行学习引擎与 CLI 私有进度存储。"""

import datetime
from portable_compat import file_locks as fcntl
import json
import os
from portable_compat import private_file
from pathlib import Path
import re
import tempfile


VERSION = 1
MAX_SAFE_INTEGER = 9007199254740991
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_DRAFT_LENGTH = 300
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def fresh_state():
    return {"version": 1, "mode": "copy", "filter": "all", "current": "", "draft": "", "revealed": False, "hadError": False, "phase": "typing", "records": {}, "days": {}}


def load_words(path):
    path = Path(path)
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("词库文件超过 5 MB")
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("词库读取失败: %s" % exc)
    if not isinstance(data, list):
        raise ValueError("词库必须是数组")
    words = []
    seen = set()
    for word in data:
        if not isinstance(word, dict) or not isinstance(word.get("name"), str) or not word["name"] or word["name"] in seen:
            continue
        if not isinstance(word.get("trans"), list) or not all(isinstance(item, str) for item in word["trans"]):
            continue
        seen.add(word["name"])
        words.append(word)
    if not words:
        raise ValueError("词库为空")
    return words


def _valid_state(state):
    if not isinstance(state, dict) or isinstance(state.get("version"), bool) or state.get("version") != VERSION:
        return False
    required = {"version", "mode", "filter", "current", "draft", "revealed", "hadError", "phase", "records", "days"}
    if set(state) != required or state["mode"] not in ("copy", "recall") or state["filter"] not in ("all", "errors"):
        return False
    if not isinstance(state["current"], str) or not isinstance(state["draft"], str) or len(state["draft"]) > MAX_DRAFT_LENGTH:
        return False
    if state["phase"] not in ("typing", "success") or not isinstance(state["revealed"], bool) or not isinstance(state["hadError"], bool):
        return False
    if not isinstance(state["records"], dict) or not isinstance(state["days"], dict):
        return False
    for record in state["records"].values():
        if not isinstance(record, dict) or set(record) != {"correct", "mistakes", "recall", "needsReview"}:
            return False
        for key in ("correct", "mistakes", "recall"):
            if isinstance(record[key], bool) or not isinstance(record[key], int) or not 0 <= record[key] <= MAX_SAFE_INTEGER:
                return False
        if not isinstance(record["needsReview"], bool):
            return False
    for day, count in state["days"].items():
        if not isinstance(day, str) or not DATE_RE.fullmatch(day) or isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= MAX_SAFE_INTEGER:
            return False
    return len(json.dumps(state, ensure_ascii=False).encode("utf-8")) <= MAX_FILE_BYTES


def _require_state(state):
    if not _valid_state(state):
        raise ValueError("进度文件不是有效的 version 1 状态")
    return state


class StudySession:
    def __init__(self, words, state=None, chapter_size=20):
        if not isinstance(words, list):
            raise ValueError("词库必须是数组")
        self._set_chapter_size(chapter_size)
        self.words = words
        self.state = _require_state(state if state is not None else fresh_state())
        if self.state["current"] not in {word["name"] for word in words}:
            self.state["current"] = self._pool()[0]["name"] if self._pool() else ""
            self._reset_answer()
        self.message = ""
        self.tone = ""
        self._refresh_message()

    def _set_chapter_size(self, chapter_size):
        if isinstance(chapter_size, bool) or not isinstance(chapter_size, int) or not 1 <= chapter_size <= 500:
            raise ValueError("chapter_size 必须是 1 到 500 的整数")
        self.chapter_size = chapter_size

    @property
    def current_word(self):
        for word in self.words:
            if word["name"] == self.state["current"]:
                return word
        return None

    @property
    def position(self):
        current = self.current_word
        return self.words.index(current) + 1 if current else 0

    @property
    def error_count(self):
        return sum(1 for word in self.words if self.state["records"].get(word["name"], {}).get("needsReview") is True)

    @property
    def today_count(self):
        return self.state["days"].get(self._today(), 0)

    @property
    def chapter_index(self):
        return (self.position - 1) // self.chapter_size if self.position else 0

    @property
    def chapter_count(self):
        return (len(self.words) + self.chapter_size - 1) // self.chapter_size if self.words else 0

    @property
    def chapter_position(self):
        return (self.position - 1) % self.chapter_size + 1 if self.position else 0

    @property
    def chapter_word_count(self):
        if not self.words:
            return 0
        start = self.chapter_index * self.chapter_size
        return min(self.chapter_size, len(self.words) - start)

    def _check_chapter_index(self, index):
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < self.chapter_count:
            raise ValueError("章节索引超出范围")

    def select_chapter(self, index):
        self._check_chapter_index(index)
        self.state["filter"] = "all"
        self.state["current"] = self.words[index * self.chapter_size]["name"]
        self._reset_answer()
        self._refresh_message()

    def chapter_info(self, index):
        self._check_chapter_index(index)
        start_index = index * self.chapter_size
        end_index = min(start_index + self.chapter_size, len(self.words))
        chapter_words = self.words[start_index:end_index]
        practiced = sum(1 for word in chapter_words if self.state["records"].get(word["name"], {}).get("correct", 0) > 0)
        return {"index": index, "start": start_index + 1, "end": end_index, "total": len(chapter_words), "practiced": practiced, "first": chapter_words[0]["name"], "last": chapter_words[-1]["name"]}

    @staticmethod
    def _today():
        return datetime.date.today().isoformat()

    @staticmethod
    def _normal(value):
        return " ".join(value.strip().lower().split())

    def _pool(self):
        if self.state["filter"] == "errors":
            return [word for word in self.words if self.state["records"].get(word["name"], {}).get("needsReview") is True]
        return self.words

    def _record(self, name):
        if name not in self.state["records"]:
            self.state["records"][name] = {"correct": 0, "mistakes": 0, "recall": 0, "needsReview": False}
        return self.state["records"][name]

    def _reset_answer(self):
        self.state.update({"draft": "", "revealed": False, "hadError": False, "phase": "typing"})

    def _refresh_message(self):
        if self.state["phase"] == "success":
            self.message, self.tone = "拼写正确。再次提交进入下一词。", "good"
        elif self.current_word is None:
            self.message, self.tone = "没有待练习的词。", ""
        else:
            self.message = "对照拼写；需要回忆时切到「默写」。" if self.state["mode"] == "copy" else "根据释义默写；Enter 检查。"
            self.tone = ""

    def set_draft(self, text):
        self.state["draft"] = str(text)[:MAX_DRAFT_LENGTH]
        self.state["phase"] = "typing"
        self._refresh_message()

    def submit(self):
        if self.state["phase"] == "success":
            return self.next_word()
        current = self.current_word
        if current is None:
            self.message, self.tone = "没有待练习的词。", "bad"
            return
        if not self._normal(self.state["draft"]):
            self.message, self.tone = "先输入一个单词。", "bad"
            return
        record = self._record(current["name"])
        if self._normal(self.state["draft"]) == self._normal(current["name"]):
            record["correct"] += 1
            if self.state["mode"] == "recall" and not self.state["revealed"] and not self.state["hadError"]:
                record["recall"] += 1
                record["needsReview"] = False
            self.state["phase"] = "success"
            day = self._today()
            self.state["days"][day] = self.state["days"].get(day, 0) + 1
            self.message, self.tone = "拼写正确。再次提交进入下一词。", "good"
        else:
            if not self.state["hadError"]:
                record["mistakes"] += 1
            record["needsReview"] = True
            self.state["hadError"] = True
            self.message, self.tone = "拼写还不对，已加入错词。", "bad"

    def next_word(self):
        pool = self._pool()
        if not pool:
            self.state["current"] = ""
            self._reset_answer()
            self._refresh_message()
            return
        names = [word["name"] for word in pool]
        try:
            index = names.index(self.state["current"])
            target = pool[(index + 1) % len(pool)]
        except ValueError:
            target = pool[0]
        self.state["current"] = target["name"]
        self._reset_answer()
        self._refresh_message()

    def toggle_mode(self):
        self.state["mode"] = "recall" if self.state["mode"] == "copy" else "copy"
        self._reset_answer()
        self._refresh_message()

    def toggle_errors(self):
        self.state["filter"] = "errors" if self.state["filter"] == "all" else "all"
        pool = self._pool()
        self.state["current"] = pool[0]["name"] if pool else ""
        self._reset_answer()
        self._refresh_message()

    def reveal(self):
        current = self.current_word
        if current is None or self.state["mode"] != "recall" or self.state["revealed"] or self.state["phase"] == "success":
            return
        self.state["revealed"] = True
        self._record(current["name"])["needsReview"] = True
        self.message, self.tone = "先记一下拼写；这个词已加入错词，之后再默写。", ""


class StateStore:
    def __init__(self, data_dir, import_from=None):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.path = self.data_dir / "progress.json"
        self.lock_path = self.data_dir / "progress.json.lock"
        self.import_from = Path(import_from).expanduser().resolve() if import_from else None
        self._lock = None

    def __enter__(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = open(self.lock_path, "a+b")
        os.chmod(self.lock_path, 0o600)
        try:
            fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._lock.close()
            self._lock = None
            raise RuntimeError("已有 CLI 学习终端正在使用此数据目录")
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self._lock:
            fcntl.flock(self._lock.fileno(), fcntl.LOCK_UN)
            self._lock.close()
            self._lock = None

    def _load_unlocked(self):
        if self.path.exists():
            try:
                if self.path.stat().st_size > MAX_FILE_BYTES:
                    raise ValueError("CLI 进度文件超过 5 MB")
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and set(raw) == {"version", "revision", "progress"} and not isinstance(raw["version"], bool) and raw["version"] == VERSION and isinstance(raw["revision"], int) and not isinstance(raw["revision"], bool) and 0 <= raw["revision"] <= MAX_SAFE_INTEGER:
                    raw = raw["progress"]
                    if raw is None:
                        return fresh_state()
                return _require_state(raw)
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                raise ValueError("CLI 进度损坏: %s" % exc)
        if self.import_from and self.import_from.exists():
            try:
                if self.import_from.stat().st_size > MAX_FILE_BYTES:
                    raise ValueError("MCP 进度文件超过 5 MB")
                envelope = json.loads(self.import_from.read_text(encoding="utf-8"))
                if isinstance(envelope, dict) and set(envelope) == {"version", "revision", "progress"} and not isinstance(envelope.get("version"), bool) and envelope.get("version") == VERSION and isinstance(envelope.get("revision"), int) and not isinstance(envelope.get("revision"), bool) and 0 <= envelope["revision"] <= MAX_SAFE_INTEGER:
                    progress = envelope["progress"]
                elif isinstance(envelope, dict) and "version" in envelope:
                    progress = envelope
                else:
                    progress = None
                if progress is not None:
                    _require_state(progress)
                else:
                    progress = fresh_state()
            except (OSError, json.JSONDecodeError, ValueError, AttributeError) as exc:
                raise ValueError("MCP 进度导入失败: %s" % exc)
            self._write_unlocked(progress)
            self._atomic_write(self.data_dir / "migration.json", {"source": str(self.import_from), "version": VERSION, "migratedAt": datetime.datetime.now(datetime.timezone.utc).isoformat()})
            return progress
        return fresh_state()

    def load(self):
        if self._lock:
            return self._load_unlocked()
        with self:
            return self._load_unlocked()

    def _atomic_write(self, path, value):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".progress-", suffix=".tmp", dir=str(path.parent))
        try:
            private_file(fd)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _write_unlocked(self, state):
        self._atomic_write(self.path, state)

    def save(self, state):
        _require_state(state)
        if self._lock:
            self._write_unlocked(state)
        else:
            with self:
                self._write_unlocked(state)
