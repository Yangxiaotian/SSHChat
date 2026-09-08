"""Chess clock state machine used by the Kindle web page."""

import time
import unittest

import clock_sharing


class ClockTests(unittest.TestCase):
    def _session(self, base_ms=60_000, inc_ms=5_000):
        return clock_sharing.clock_store.create_session(
            creator="ada",
            room="play",
            base_ms=base_ms,
            inc_ms=inc_ms,
        )

    def test_parse_time_spec(self):
        self.assertEqual(clock_sharing.parse_time_spec("10"), (600_000, 0))
        self.assertEqual(clock_sharing.parse_time_spec("10+5"), (600_000, 5_000))
        self.assertEqual(clock_sharing.parse_time_spec("1h"), (3_600_000, 0))
        self.assertIsNone(clock_sharing.parse_time_spec("0"))
        self.assertIsNone(clock_sharing.parse_time_spec("nope"))

    def test_hit_adds_increment_and_switches(self):
        session = self._session()
        now = 1_000.0
        self.assertEqual(session.hit("red", now), "")
        self.assertEqual(session.running, "red")
        self.assertEqual(session.hit("red", now + 10), "")
        self.assertEqual(session.running, "black")
        self.assertEqual(session.red_ms, 50_000 + 5_000)

    def test_flag_on_timeout(self):
        session = self._session(base_ms=30_000, inc_ms=0)
        now = 2_000.0
        session.start("black", now)
        session.settle(now + 31)
        self.assertEqual(session.flagged, "black")
        self.assertIsNone(session.running)
        self.assertEqual(session.black_ms, 0)

    def test_page_has_no_script(self):
        import clock_http

        session = self._session()
        page = clock_http.render_clock_page(session, "zh")
        self.assertNotIn("<script", page.lower())
        self.assertIn("红方 开始计时", page)
        self.assertNotIn("http-equiv=\"refresh\"", page)
        session.start("red", time.time())
        page = clock_http.render_clock_page(session, "zh")
        self.assertIn("http-equiv=\"refresh\"", page)
        self.assertIn("红方 走完了", page)


if __name__ == "__main__":
    unittest.main()
