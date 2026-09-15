#!/usr/bin/env python3
"""基于 SQLite 的轻量复习调度后端；不宣称 FSRS。"""

import datetime
import json
import math
import os
from pathlib import Path
import sqlite3
import time


MODES = {"copy", "recall", "en_to_zh", "zh_to_en", "listening", "cloze", "collocation"}
CONTEXTS = {"learn", "review", "repair"}
DEFAULT_INTERVALS = [1, 3, 7, 14, 30]


def _int(value, name, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError("%s 必须是整数" % name)
    return value


class StudyPlanner:
    def __init__(self, data_dir, clock=time.time):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.data_dir, 0o700)
        self.path = self.data_dir / "learning.sqlite3"
        self.clock = clock
        self.intervals = list(DEFAULT_INTERVALS)
        existed = self.path.exists() and self.path.stat().st_size > 0
        try:
            self.db = sqlite3.connect(str(self.path), timeout=5, isolation_level=None)
            self.db.execute("PRAGMA busy_timeout=5000")
            self.db.execute("PRAGMA foreign_keys=ON")
            self._schema(existed)
            os.chmod(self.path, 0o600)
        except Exception:
            if hasattr(self, "db"):
                self.db.close()
            raise ValueError("学习数据库读取或迁移失败；原数据库未覆盖" if existed else "学习数据库初始化失败")
        self._closed = False

    def _schema(self, existed):
        if existed:
            marker = self.db.execute("SELECT value FROM meta WHERE key='application_id'").fetchone() if self._has_table("meta") else None
            version = self.db.execute("SELECT value FROM meta WHERE key='user_version'").fetchone() if marker else None
            if not marker or marker[0] != "ielts-cli" or not version or version[0] != "1":
                raise ValueError("未知学习数据库，已拒绝修改")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS cards (
                dictionary_id TEXT NOT NULL,
                word TEXT NOT NULL,
                due REAL NOT NULL,
                stage INTEGER NOT NULL,
                last_seen REAL,
                seeded INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (dictionary_id, word)
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE,
                dictionary_id TEXT NOT NULL,
                word TEXT NOT NULL,
                mode TEXT NOT NULL,
                correct INTEGER NOT NULL,
                hinted INTEGER NOT NULL,
                context TEXT NOT NULL,
                happened_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_cards_due ON cards(due, dictionary_id);
            CREATE INDEX IF NOT EXISTS idx_events_day ON events(happened_at);
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        if not existed:
            self.db.execute("INSERT INTO meta(key,value) VALUES('application_id','ielts-cli')")
            self.db.execute("INSERT INTO meta(key,value) VALUES('user_version','1')")
            self._save_intervals()
        self._load_intervals()
        for stage, in self.db.execute("SELECT stage FROM cards"):
            if isinstance(stage, bool) or not isinstance(stage, int) or stage < 0 or stage > len(self.intervals):
                raise ValueError("学习数据库中的 stage 无效")

    def _has_table(self, name):
        return self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None

    def _save_intervals(self):
        self.db.execute("INSERT INTO meta(key,value) VALUES('intervals',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(self.intervals),))

    def _load_intervals(self):
        row = self.db.execute("SELECT value FROM meta WHERE key='intervals'").fetchone()
        if row:
            try:
                value = json.loads(row[0])
                if not isinstance(value, list) or not value or any(isinstance(item, bool) or not isinstance(item, int) or item < 1 for item in value) or any(value[i] >= value[i + 1] for i in range(len(value) - 1)):
                    raise ValueError
                self.intervals = value
            except (ValueError, TypeError, json.JSONDecodeError):
                raise ValueError("学习数据库中的复习间隔无效")

    def _now(self, now=None):
        value = self.clock() if now is None else now
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("时间必须是数字")
        if not math.isfinite(value) or value < 0:
            raise ValueError("时间必须是有限的非负数字")
        return float(value)

    def _check_key(self, value, name):
        if not isinstance(value, str) or not value or len(value) > 512:
            raise ValueError("%s 无效" % name)

    def get_intervals(self):
        return list(self.intervals)

    def set_intervals(self, intervals):
        if not isinstance(intervals, list) or not intervals or len(intervals) > 32:
            raise ValueError("复习间隔必须是非空数组")
        checked = [_int(item, "复习间隔", 1) for item in intervals]
        if any(item > 36500 for item in checked):
            raise ValueError("复习间隔不能超过 36500 天")
        if any(checked[i] >= checked[i + 1] for i in range(len(checked) - 1)):
            raise ValueError("复习间隔必须严格递增")
        self.intervals = checked
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self._save_intervals()
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def seed_from_library(self, profiles):
        if not isinstance(profiles, dict):
            raise ValueError("profiles 必须是字典")
        now = self._now()
        count = 0
        self.db.execute("BEGIN IMMEDIATE")
        try:
            for dictionary_id, state in profiles.items():
                self._check_key(dictionary_id, "dictionary_id")
                if not isinstance(state, dict) or not isinstance(state.get("records"), dict):
                    raise ValueError("旧词库进度格式无效")
                for word in state["records"]:
                    self._check_key(word, "word")
                    cursor = self.db.execute("INSERT OR IGNORE INTO cards(dictionary_id,word,due,stage,last_seen,seeded) VALUES(?,?,?,?,NULL,1)", (dictionary_id, word, now, 0))
                    count += cursor.rowcount
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        return count

    def due_cards(self, dictionary_id=None, limit=200, now=None):
        if dictionary_id is not None:
            self._check_key(dictionary_id, "dictionary_id")
        _int(limit, "limit", 1)
        if limit > 200:
            raise ValueError("limit 不能超过 200")
        moment = self._now(now)
        query = "SELECT dictionary_id,word,due,stage FROM cards WHERE due<=?"
        args = [moment]
        if dictionary_id is not None:
            query += " AND dictionary_id=?"
            args.append(dictionary_id)
        query += " ORDER BY due, dictionary_id, word LIMIT ?"
        args.append(limit)
        result = []
        for row in self.db.execute(query, args):
            if isinstance(row[3], bool) or not isinstance(row[3], int) or row[3] < 0 or row[3] > len(self.intervals):
                raise ValueError("学习数据库中的 stage 无效")
            result.append({"dictionary_id": row[0], "word": row[1], "due": row[2], "stage": row[3]})
        return result

    def note(self, dictionary_id, word, mode, correct, hinted=False, context="learn", event_id=None, now=None):
        self._check_key(dictionary_id, "dictionary_id")
        self._check_key(word, "word")
        if mode not in MODES:
            raise ValueError("mode 无效")
        if context not in CONTEXTS:
            raise ValueError("context 无效")
        if not isinstance(correct, bool) or not isinstance(hinted, bool):
            raise ValueError("correct/hinted 必须是布尔值")
        if event_id is not None:
            self._check_key(event_id, "event_id")
        moment = self._now(now)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            existing = self.db.execute("SELECT dictionary_id,word,mode,correct,hinted,context FROM events WHERE event_id=?", (event_id,)).fetchone() if event_id else None
            if existing:
                if existing != (dictionary_id, word, mode, int(correct), int(hinted), context):
                    raise ValueError("event_id 已被不同事件使用")
                card = self._card(dictionary_id, word)
                self.db.execute("COMMIT")
                return card
            row = self.db.execute("SELECT due,stage FROM cards WHERE dictionary_id=? AND word=?", (dictionary_id, word)).fetchone()
            if row is None:
                self.db.execute("INSERT INTO cards(dictionary_id,word,due,stage,last_seen,seeded) VALUES(?,?,?,?,NULL,0)", (dictionary_id, word, moment, 0))
                stage = 0
                was_existing = False
            else:
                stage = row[1]
                was_existing = True
            self.db.execute("INSERT INTO events(event_id,dictionary_id,word,mode,correct,hinted,context,happened_at) VALUES(?,?,?,?,?,?,?,?)", (event_id, dictionary_id, word, mode, int(correct), int(hinted), context, moment))
            if context == "repair":
                due, new_stage = moment + 600, stage
            elif correct and mode in {"copy", "en_to_zh", "zh_to_en"}:
                new_stage, due = stage, row[0] if was_existing else moment + 600
            elif correct and mode in {"recall", "listening", "cloze", "collocation"} and not hinted:
                new_stage = min(stage + 1, len(self.intervals))
                due = moment + self.intervals[new_stage - 1] * 86400
            else:
                new_stage, due = 0, moment + 600
            self.db.execute("UPDATE cards SET due=?,stage=?,last_seen=? WHERE dictionary_id=? AND word=?", (due, new_stage, moment, dictionary_id, word))
            self.db.execute("COMMIT")
            return self._card(dictionary_id, word)
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def _card(self, dictionary_id, word):
        row = self.db.execute("SELECT dictionary_id,word,due,stage,last_seen FROM cards WHERE dictionary_id=? AND word=?", (dictionary_id, word)).fetchone()
        return {"dictionary_id": row[0], "word": row[1], "due": row[2], "stage": row[3]} if row else None

    def mode_stats(self, dictionary_id, word):
        self._check_key(dictionary_id, "dictionary_id")
        self._check_key(word, "word")
        result = {mode: {"correct": 0, "wrong": 0, "unassisted_correct": 0} for mode in sorted(MODES)}
        for mode, correct, total in self.db.execute("SELECT mode,correct,COUNT(*) FROM events WHERE dictionary_id=? AND word=? GROUP BY mode,correct", (dictionary_id, word)):
            result[mode]["correct" if correct else "wrong"] = total
        for mode, total in self.db.execute("SELECT mode,COUNT(*) FROM events WHERE dictionary_id=? AND word=? AND correct=1 AND hinted=0 GROUP BY mode", (dictionary_id, word)):
            result[mode]['unassisted_correct'] = total
        return result

    def stats(self, day=None):
        if day is None:
            day = datetime.date.fromtimestamp(self._now()).isoformat()
        if not isinstance(day, str) or not datetime.datetime.strptime(day, "%Y-%m-%d"):
            raise ValueError("day 必须是 YYYY-MM-DD")
        date = datetime.datetime.strptime(day, "%Y-%m-%d").date()
        start = datetime.datetime.combine(date, datetime.time.min).timestamp()
        end = datetime.datetime.combine(date + datetime.timedelta(days=1), datetime.time.min).timestamp()
        rows = self.db.execute("SELECT dictionary_id,word,mode,correct,context FROM events WHERE happened_at>=? AND happened_at<?", (start, end)).fetchall()
        by_mode = {mode: {"attempts": 0, "correct": 0, "wrong": 0} for mode in sorted(MODES)}
        seen_learn, seen_review = set(), set()
        first_learn = {(dictionary_id, word): happened_at for dictionary_id, word, happened_at in self.db.execute("SELECT dictionary_id,word,MIN(happened_at) FROM events WHERE context='learn' GROUP BY dictionary_id,word")}
        correct = 0
        for dictionary_id, word, mode, is_correct, context in rows:
            bucket = by_mode[mode]
            bucket["attempts"] += 1
            bucket["correct" if is_correct else "wrong"] += 1
            correct += is_correct
            if context == "learn":
                key = (dictionary_id, word)
                seeded = self.db.execute("SELECT seeded FROM cards WHERE dictionary_id=? AND word=?", key).fetchone()
                if (not seeded or seeded[0] == 0) and first_learn.get(key) is not None and start <= first_learn[key] < end:
                    seen_learn.add(key)
            if context == "review":
                seen_review.add((dictionary_id, word))
        due = self.db.execute("SELECT COUNT(*) FROM cards WHERE due<=?", (self._now(),)).fetchone()[0]
        return {"new_words": len(seen_learn), "review_words": len(seen_review), "attempts": len(rows), "correct": correct, "due": due, "by_mode": by_mode}

    def close(self):
        if not self._closed:
            self.db.close()
            self._closed = True
