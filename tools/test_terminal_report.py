import json
import tempfile
import unittest
from pathlib import Path

from terminal_report import write_report


class TerminalReportTests(unittest.TestCase):
    def test_outputs_escape_data_and_render_grid(self):
        report = {"schema_version": 1, "platform": "test", "python": "3", "backend": "pyte", "passed": True, "steps": [], "failure": None, "limits": []}
        frames = [{"time": 1.25, "step": "bad </script> \u2028", "rows": 2, "cols": 4, "lines": ["<b>x</b>", "ab  "]}]
        with tempfile.TemporaryDirectory() as directory:
            write_report(Path(directory), report, frames)
            html = (Path(directory) / "replay.html").read_text(encoding="utf-8")
            self.assertIn("textContent", html)
            self.assertNotIn("</script>", html.split('id="report-data"', 1)[1].split("</script>", 1)[0])
            self.assertIn("\\u003c/script\\u003e", html)
            self.assertIn('"cols":4', html)
            self.assertEqual(json.loads((Path(directory) / "frames.json").read_text()), frames)
            self.assertIn("bad </script>", (Path(directory) / "screens.txt").read_text(encoding="utf-8"))

    def test_empty_frames_make_failure_replay(self):
        report = {"schema_version": 1, "platform": "test", "python": "3", "backend": "pty", "passed": False, "steps": [], "failure": "no frames", "limits": []}
        with tempfile.TemporaryDirectory() as directory:
            write_report(Path(directory), report, [])
            html = (Path(directory) / "replay.html").read_text(encoding="utf-8")
            self.assertIn("没有捕获帧", html)
            self.assertEqual((Path(directory) / "screens.txt").read_text(encoding="utf-8"), "")


if __name__ == "__main__":
    unittest.main()
