import tempfile
import unittest
from pathlib import Path

from routine import Routine


class RoutineTests(unittest.TestCase):
    def test_persist_validate_tick_and_summary(self):
        now = [1000.0]
        with tempfile.TemporaryDirectory() as directory:
            routine = Routine(directory, clock=lambda: now[0])
            self.assertEqual(routine.config["new_goal"], 20)
            routine.update(new_goal=10, review_goal=4, batch_size=3)
            self.assertTrue(Path(directory, "routine.json").is_file())
            self.assertFalse(routine.tick(True))
            now[0] += 1801
            self.assertTrue(routine.tick(True))
            self.assertFalse(routine.tick(True))
            now[0] += 1801
            self.assertTrue(routine.tick(True))
            self.assertEqual(routine.summary({"new_words": 2, "review_words": 9})["remaining_new"], 8)

    def test_snooze_and_reject_invalid_values(self):
        now = [1000.0]
        with tempfile.TemporaryDirectory() as directory:
            routine = Routine(directory, clock=lambda: now[0])
            routine.snooze(15)
            self.assertFalse(routine.tick(True))
            with self.assertRaises(ValueError):
                routine.update(batch_size=0)


if __name__ == "__main__":
    unittest.main()
