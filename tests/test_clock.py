"""Chess clock state machine used by the Kindle web page."""

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
        self.assertEqual(session.hit("top", now), "")
        self.assertEqual(session.running, "top")
        self.assertEqual(session.hit("top", now + 10), "")
        self.assertEqual(session.running, "bottom")
        self.assertEqual(session.top_ms, 50_000 + 5_000)

    def test_flag_on_timeout(self):
        session = self._session(base_ms=30_000, inc_ms=0)
        now = 2_000.0
        session.start("bottom", now)
        session.settle(now + 31)
        self.assertEqual(session.flagged, "bottom")
        self.assertIsNone(session.running)
        self.assertEqual(session.bottom_ms, 0)

    def test_page_has_no_script(self):
        import clock_http

        session = self._session()
        page = clock_http.render_clock_page(session, "zh")
        self.assertIn("setInterval", page)
        self.assertIn("上方", page)
        self.assertNotIn("红方", page)
        self.assertNotIn("黑方", page)
        self.assertNotIn("http-equiv=\"refresh\"", page)
        # Setup panel must include its own close control (mid bar used to be covered).
        self.assertIn('id="setup"', page)
        self.assertIn('class="close"', page)
        self.assertIn("toggleSetup()", page)


if __name__ == "__main__":
    unittest.main()
