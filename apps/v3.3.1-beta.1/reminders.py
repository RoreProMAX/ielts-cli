#!/usr/bin/env python3
"""V3 可选 systemd user timer 与到期复习通知；默认不安装、不启用。"""

import datetime
from portable_compat import file_locks as fcntl
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

from routine import Routine


UNIT_NAME = "ielts-cli-portable-review"


def _script_path(executable):
    path = Path(executable).expanduser().resolve()
    if path.name != "ielts.py" or any(char in str(path) for char in "\x00\r\n"):
        raise ValueError("executable 必须指向 releaseRoot/ielts.py")
    return path


def _systemd_arg(value):
    value = str(value)
    if any(char in value for char in "\x00\r\n"):
        raise ValueError("systemd 参数不能包含换行或 NUL")
    return '"' + value.replace("%", "%%").replace('$', '$$').replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_units(executable, data_dir, minutes):
    if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes < 1:
        raise ValueError("minutes 必须是正整数")
    script = _script_path(executable)
    directory = Path(data_dir).expanduser().resolve()
    command = "%s %s --notify-review --data-dir %s" % (_systemd_arg(sys.executable), _systemd_arg(script), _systemd_arg(directory))
    service = """[Unit]\nDescription=IELTS CLI review notification\n\n[Service]\nType=oneshot\nExecStart=%s\n""" % command
    timer = """[Unit]\nDescription=IELTS CLI review timer\n\n[Timer]\nOnActiveSec=%dm\nOnUnitActiveSec=%dm\nUnit=%s.service\nPersistent=false\n\n[Install]\nWantedBy=default.target\n""" % (minutes, minutes, UNIT_NAME)
    return service, timer


def _unit_dir():
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "systemd" / "user"


def _atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _backup(path):
    if path.exists():
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        backup = path.with_name(path.name + ".bak." + stamp + "." + str(os.getpid()))
        while backup.exists():
            stamp += "x"
            backup = path.with_name(path.name + ".bak." + stamp + "." + str(os.getpid()))
        backup.write_bytes(path.read_bytes())
        return backup
    return None


def enable(data_dir, executable, minutes):
    service, timer = render_units(executable, data_dir, minutes)
    directory = _unit_dir()
    service_path, timer_path = directory / (UNIT_NAME + ".service"), directory / (UNIT_NAME + ".timer")
    originals = {path: path.read_bytes() if path.exists() else None for path in (service_path, timer_path)}
    states = {"enabled": _unit_state(timer_path.name, "is-enabled"), "active": _unit_state(timer_path.name, "is-active")}
    backups = [_backup(service_path), _backup(timer_path)]
    try:
        _atomic_write(service_path, service)
        _atomic_write(timer_path, timer)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True, timeout=10)
        subprocess.run(["systemctl", "--user", "enable", "--now", UNIT_NAME + ".timer"], check=True, timeout=10)
    except Exception:
        for path, content in originals.items():
            if content is None:
                if path.exists():
                    path.unlink()
            else:
                _atomic_write(path, content.decode("utf-8"))
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False, timeout=10)
        subprocess.run(["systemctl", "--user", "enable" if states["enabled"] else "disable", UNIT_NAME + ".timer"], check=False, timeout=10)
        subprocess.run(["systemctl", "--user", "start" if states["active"] else "stop", UNIT_NAME + ".timer"], check=False, timeout=10)
        raise
    return {"enabled": True, "timer": str(timer_path), "backups": [str(path) for path in backups if path]}


def disable():
    subprocess.run(["systemctl", "--user", "disable", "--now", UNIT_NAME + ".timer"], check=True, timeout=10)
    return {"disabled": True, "timer": UNIT_NAME + ".timer"}


def status():
    result = subprocess.run(["systemctl", "--user", "status", UNIT_NAME + ".timer", "--no-pager"], capture_output=True, text=True, timeout=10)
    return {"unit": UNIT_NAME + ".timer", "returncode": result.returncode, "active": result.returncode == 0, "stdout": result.stdout, "stderr": result.stderr}


def _unit_state(unit, action):
    result = subprocess.run(["systemctl", "--user", action, unit], capture_output=True, text=True, timeout=10)
    return result.returncode == 0


def _due_count(path, now=None):
    now = datetime.datetime.now(datetime.timezone.utc) if now is None else now
    connection = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        marker = connection.execute("SELECT value FROM meta WHERE key='application_id'").fetchone()
        version = connection.execute("SELECT value FROM meta WHERE key='user_version'").fetchone()
        if not marker or marker[0] != "ielts-cli" or not version or version[0] != "1":
            return 0
        return int(connection.execute("SELECT COUNT(*) FROM cards WHERE due<=?", (now.timestamp(),)).fetchone()[0])
    finally:
        connection.close()


def notify_review(data_dir):
    directory = Path(data_dir).expanduser().resolve()
    lock_path = directory / "progress.json.lock"
    if lock_path.exists():
        with open(lock_path, "a+b") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"notified": False, "due": 0, "quiet": True, "reason": "cli_running"}
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    routine = Routine(directory)
    if routine.config["quiet_until"] > __import__("time").time():
        return {"notified": False, "due": 0, "quiet": True, "reason": "snoozed"}
    database = directory / "learning.sqlite3"
    if not database.is_file():
        return {"notified": False, "due": 0, "quiet": True, "reason": "database_missing"}
    due = _due_count(database)
    if due <= 0:
        return {"notified": False, "due": 0, "quiet": True}
    message = "有 %d 个词到期复习，请打开 IELTS 分享包并用 --review 启动" % due
    subprocess.run(["notify-send", "IELTS CLI", message], check=True, timeout=5)
    return {"notified": True, "due": due, "message": message}
