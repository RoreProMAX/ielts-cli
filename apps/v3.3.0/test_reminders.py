import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reminders import disable, enable, notify_review, render_units, status


class ReminderTests(unittest.TestCase):
    def test_render_units_quotes_paths_and_uses_review_command(self):
        service, timer = render_units("/tmp/release root/ielts.py", "/tmp/data dir", 30)
        self.assertIn("--notify-review", service)
        self.assertIn("/tmp/release root/ielts.py", service)
        self.assertIn("OnActiveSec=30m", timer)
        self.assertIn("OnUnitActiveSec=30m", timer)
        self.assertIn("ielts-cli-portable-review.service", timer)

    def test_enable_disable_status_are_scoped_and_mocked(self):
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            return type("Result", (), {"returncode": 0, "stdout": "active", "stderr": ""})()
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"XDG_CONFIG_HOME": home}), patch("reminders.subprocess.run", side_effect=run):
            enabled = enable(directory, "/tmp/release/ielts.py", 30)
            self.assertTrue(enabled["enabled"])
            self.assertTrue(Path(home, "systemd/user/ielts-cli-portable-review.timer").is_file())
            self.assertEqual(disable()["timer"], "ielts-cli-portable-review.timer")
            self.assertTrue(status()["active"])
        self.assertIn("daemon-reload", calls[2])
        self.assertIn("--now", calls[3])
        self.assertIn("--now", calls[4])

    def test_notify_due_and_quiet_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "learning.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute("INSERT INTO meta VALUES ('application_id', 'ielts-cli')")
            connection.execute("INSERT INTO meta VALUES ('user_version', '1')")
            connection.execute("CREATE TABLE cards (word TEXT, due REAL)")
            connection.execute("INSERT INTO cards VALUES ('one', 0)")
            connection.commit()
            connection.close()
            with patch("reminders.subprocess.run") as run:
                result = notify_review(directory)
            self.assertTrue(result["notified"])
            run.assert_called_once()
            self.assertEqual(run.call_args.args[0][0], "notify-send")


if __name__ == "__main__":
    unittest.main()
