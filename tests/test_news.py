"""RSS news listing includes the original article URL."""

import unittest
from unittest import mock


class NewsOriginalLinkTests(unittest.TestCase):
    def test_section_emits_http_link(self) -> None:
        import server

        sent: list[str] = []
        items = [
            {
                "source": "BBC",
                "title": "Hello",
                "body": "A short summary.",
                "link": "https://www.bbc.com/news/hello",
            }
        ]
        with mock.patch.object(server, "send_line", lambda _conn, line: sent.append(line)):
            with mock.patch.object(server, "_get_news_items", return_value=items):
                server._send_news_section(object(), "world", 3)
        blob = "".join(sent)
        self.assertIn("1. [BBC] Hello", blob)
        self.assertIn("https://www.bbc.com/news/hello", blob)
        self.assertNotIn("javascript:", blob)

    def test_skips_non_http_link(self) -> None:
        import server

        sent: list[str] = []
        items = [
            {
                "source": "X",
                "title": "Nope",
                "body": "n",
                "link": "javascript:alert(1)",
            }
        ]
        with mock.patch.object(server, "send_line", lambda _conn, line: sent.append(line)):
            with mock.patch.object(server, "_get_news_items", return_value=items):
                server._send_news_section(object(), "tech", 1)
        self.assertNotIn("javascript:", "".join(sent))

    def test_empty_fetch_is_not_cached(self) -> None:
        import server

        server.news_cache.clear()
        server.news_fetching.clear()
        with mock.patch.object(server, "_fetch_feed", return_value=[]) as fetch:
            self.assertEqual(server._get_news_items("tech"), [])
            self.assertEqual(server._get_news_items("tech"), [])
        self.assertGreater(fetch.call_count, 1)
        self.assertNotIn("tech", server.news_cache)


if __name__ == "__main__":
    unittest.main()
