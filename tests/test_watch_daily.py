from __future__ import annotations

import unittest
from datetime import datetime

from kb.cli import _next_daily, _parse_daily


class WatchDailyTest(unittest.TestCase):
    def test_parse_valid(self) -> None:
        self.assertEqual(_parse_daily("10:00"), (10, 0))
        self.assertEqual(_parse_daily(" 9:05 "), (9, 5))
        self.assertEqual(_parse_daily("23:59"), (23, 59))

    def test_parse_invalid(self) -> None:
        for value in ("1000", "24:00", "10:60", "abc", ""):
            with self.assertRaises(ValueError):
                _parse_daily(value)

    def test_next_daily_skips_missed_run(self) -> None:
        early = datetime(2026, 11, 3, 9, 0)
        self.assertEqual(_next_daily(early, 10, 0), datetime(2026, 11, 3, 10, 0))

        late = datetime(2026, 11, 3, 10, 30)
        self.assertEqual(_next_daily(late, 10, 0), datetime(2026, 11, 4, 10, 0))

        exact = datetime(2026, 11, 3, 10, 0)
        self.assertEqual(_next_daily(exact, 10, 0), datetime(2026, 11, 4, 10, 0))


if __name__ == "__main__":
    unittest.main()
