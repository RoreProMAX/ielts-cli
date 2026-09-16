#!/usr/bin/env python3
"""V3 每日新词与复习目标；计时只存在于当前 CLI 进程。"""

import json
import math
import os
from portable_compat import private_file
from pathlib import Path
import tempfile
import time


DEFAULTS = {"version": 1, "new_goal": 20, "review_goal": 30, "batch_size": 5, "reminder_minutes": 30, "quiet_until": 0, "extra_after_daily": True, "examples_during_learning": False}
LIMITS = {"new_goal": (0, 100000), "review_goal": (0, 100000), "batch_size": (1, 200), "reminder_minutes": (0, 7 * 24 * 60), "quiet_until": (0, 4102444800)}


class Routine:
    def __init__(self, data_dir, clock=time.time):
        self.path = Path(data_dir).expanduser().resolve() / "routine.json"
        self.clock = clock
        self._config = dict(DEFAULTS)
        if self.path.exists():
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            self._validate(payload)
            self._config.update(payload)
        interval = self._config["reminder_minutes"] * 60
        self._next_prompt = self.clock() + interval if interval else None

    @property
    def config(self):
        return dict(self._config)

    @staticmethod
    def _validate(payload):
        if not isinstance(payload, dict) or isinstance(payload.get("version"), bool) or payload.get("version") != 1:
            raise ValueError("routine.json 格式无效")
        if set(payload) - set(DEFAULTS):
            raise ValueError("routine.json 包含未知字段")
        for key, (low, high) in LIMITS.items():
            value = payload.get(key, DEFAULTS[key])
            if key != "quiet_until" and (isinstance(value, bool) or type(value) is not int or not low <= value <= high):
                raise ValueError("routine 配置无效：%s" % key)
            if key == "quiet_until" and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high):
                raise ValueError("routine 配置无效：%s" % key)
        for key in ('extra_after_daily', 'examples_during_learning'):
            if type(payload.get(key, DEFAULTS[key])) is not bool:
                raise ValueError("routine 配置无效：%s" % key)

    def _write(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="." + self.path.name + ".", dir=str(self.path.parent))
        try:
            private_file(fd)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(self._config, stream, ensure_ascii=False, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def update(self, **values):
        unknown = set(values) - set(DEFAULTS) - {'version'}
        if unknown:
            raise ValueError("未知 routine 配置：%s" % ",".join(sorted(unknown)))
        candidate = dict(self._config)
        candidate.update(values)
        self._validate(candidate)
        previous = self._config
        self._config = candidate
        try:
            self._write()
        except Exception:
            self._config = previous
            raise
        if 'reminder_minutes' in values:
            minutes = self._config['reminder_minutes']
            self._next_prompt = self.clock() + minutes * 60 if minutes else None
        return self.config

    def snooze(self, minutes=15):
        if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or not 1 <= minutes <= 7 * 24 * 60:
            raise ValueError("snooze 分钟数无效")
        result = self.update(quiet_until=self.clock() + minutes * 60)
        if self._config['reminder_minutes']:
            self._next_prompt = self._config['quiet_until']
        return result

    def tick(self, has_due=True):
        now = self.clock()
        if not has_due or now < self._config["quiet_until"]:
            return False
        interval = self._config["reminder_minutes"] * 60
        if not interval or self._next_prompt is None:
            return False
        if now < self._next_prompt:
            return False
        self._next_prompt = now + interval
        return True

    def mark_review_started(self):
        minutes = self._config['reminder_minutes']
        self._next_prompt = self.clock() + minutes * 60 if minutes else None

    def summary(self, stats):
        stats = stats if isinstance(stats, dict) else {}
        new_done = max(0, int(stats.get("new_words", stats.get("new_done", 0))))
        review_done = max(0, int(stats.get("review_words", stats.get("review_done", 0))))
        return {"new_goal": self._config["new_goal"], "review_goal": self._config["review_goal"], "new_done": new_done, "review_done": review_done, "remaining_new": max(0, self._config["new_goal"] - new_done), "remaining_review": max(0, self._config["review_goal"] - review_done), "batch_size": self._config["batch_size"], "reminder_minutes": self._config["reminder_minutes"], "quiet_until": self._config["quiet_until"], "extra_after_daily": self._config['extra_after_daily'], "examples_during_learning": self._config['examples_during_learning']}
