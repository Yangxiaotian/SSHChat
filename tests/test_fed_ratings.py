"""Federated rating ledger merges prefer newer host settlements."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import federation
import server
from ratings import GameRatingStore


class RatingFederationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = GameRatingStore(str(Path(self.tmp.name) / "r.json"))
        self.prev = server.rating_store
        server.rating_store = self.store
        self.addCleanup(lambda: setattr(server, "rating_store", self.prev))
        federation._hub = None

    def test_apply_remote_prefers_newer_host_row(self) -> None:
        self.store.record_result("gomoku", "alice", "bob", 1.0)
        local = self.store.profile("gomoku", "alice")
        remote = {
            "display_name": "alice",
            "rating": local["rating"] + 50,
            "wins": local["wins"] + 1,
            "losses": 0,
            "draws": 0,
            "games": local["games"] + 1,
            "updated_at": 9_999_999_999.0,
        }
        self.assertTrue(
            self.store.apply_remote_entry("gomoku", "alice", remote, source_node="host-a")
        )
        self.assertEqual(self.store.profile("gomoku", "alice")["rating"], remote["rating"])

    def test_apply_remote_keeps_fresher_local(self) -> None:
        self.store.record_result("gomoku", "alice", "bob", 1.0)
        before = self.store.profile("gomoku", "alice")["rating"]
        stale = {
            "display_name": "alice",
            "rating": 1001,
            "wins": 0,
            "losses": 0,
            "draws": 0,
            "games": 1,
            "updated_at": 1.0,
        }
        self.assertFalse(
            self.store.apply_remote_entry("gomoku", "alice", stale, source_node="peer-b")
        )
        self.assertEqual(self.store.profile("gomoku", "alice")["rating"], before)

    def test_record_result_fans_out_via_on_change(self) -> None:
        seen: list[tuple] = []

        def _hook(game, changed):
            seen.append((game, list(changed)))

        self.store.on_change = _hook
        self.store.record_result("chess", "alice", "bob", 1.0)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][0], "chess")
        self.assertEqual(len(seen[0][1]), 2)

    def test_fed_on_ratings_applies_rows(self) -> None:
        server._fed_on_ratings(
            "Mathematics.local",
            [
                {
                    "game": "gomoku",
                    "user": "alice",
                    "display_name": "alice",
                    "rating": 1301,
                    "wins": 2,
                    "losses": 0,
                    "draws": 0,
                    "games": 2,
                    "updated_at": 12345.0,
                }
            ],
        )
        self.assertEqual(server.rating_store.profile("gomoku", "alice")["rating"], 1301)
        self.assertEqual(server.rating_store.profile("gomoku", "alice")["games"], 2)

    def test_sync_ratings_protocol_line(self) -> None:
        class FakeLink:
            def __init__(self) -> None:
                self.lines: list[str] = []

            def send_line(self, line: str) -> None:
                self.lines.append(line)

        hub = federation.FederationHub(
            12345,
            server.lock,
            lambda r, m, p: None,
            lambda r, m: None,
            lambda t, f, x: None,
            lambda: [],
        )
        hub.enabled = True
        hub.node_id = "node-a"
        link = FakeLink()
        hub._peers["node-b"] = link
        self.assertTrue(
            hub.sync_ratings(
                [
                    {
                        "game": "go",
                        "user": "alice",
                        "rating": 1210,
                        "games": 1,
                        "updated_at": 1.0,
                    }
                ]
            )
        )
        self.assertEqual(len(link.lines), 1)
        self.assertTrue(link.lines[0].startswith("rrating\tnode-a\t"))


if __name__ == "__main__":
    unittest.main()
