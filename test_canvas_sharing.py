"""Tests for shared canvas URL+key sessions and stroke sync."""

from __future__ import annotations

import os
import tempfile
import unittest

import canvas_sharing


class CanvasStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        path = os.path.join(self._tmpdir.name, "canvas.json")
        self.store = canvas_sharing.CanvasStore(store_path=path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_create_unique_tokens_and_keys(self) -> None:
        session = self.store.create_session(
            creator="Alice",
            participants=["Bob", "alice"],  # case-insensitive dedupe
            room="default",
        )
        self.assertEqual(set(session.tokens), {"Alice", "Bob"})
        self.assertEqual(len(session.tokens["Alice"]), len(session.tokens["Bob"]))
        self.assertNotEqual(session.tokens["Alice"], session.tokens["Bob"])
        self.assertNotEqual(session.keys["Alice"], session.keys["Bob"])
        self.assertEqual(len(session.keys["Alice"]), 6)

    def test_key_never_needed_in_url_and_ticket_gates_strokes(self) -> None:
        session = self.store.create_session(
            creator="Alice", participants=["Bob"], room="lab"
        )
        alice_token = session.tokens["Alice"]
        bob_token = session.tokens["Bob"]
        alice_key = session.keys["Alice"]

        # Wrong key rejected
        s, p, t, err = self.store.issue_access_ticket(alice_token, "ZZZZZZ")
        self.assertIsNone(t)
        self.assertIn("密钥", err)

        # Correct key mints multi-use ticket
        s, p, ticket, err = self.store.issue_access_ticket(alice_token, alice_key)
        self.assertEqual(err, "")
        self.assertEqual(p, "Alice")
        self.assertTrue(ticket)

        stroke, err = self.store.add_stroke(
            alice_token,
            ticket,
            color="#112233",
            width=5,
            points=[[10, 10], [20, 30], [40, 40]],
        )
        self.assertEqual(err, "")
        self.assertEqual(stroke["seq"], 1)
        self.assertEqual(stroke["author"], "Alice")

        # Bob cannot use Alice's ticket on Bob's token
        bad, err = self.store.add_stroke(
            bob_token,
            ticket,
            color="#000000",
            width=2,
            points=[[1, 1], [2, 2]],
        )
        self.assertIsNone(bad)
        self.assertTrue(err)

        # Bob auths and syncs Alice's stroke
        _, _, bob_ticket, err = self.store.issue_access_ticket(
            bob_token, session.keys["Bob"]
        )
        self.assertEqual(err, "")
        payload, err = self.store.sync_since(bob_token, bob_ticket, 0)
        self.assertEqual(err, "")
        self.assertEqual(len(payload["events"]), 1)
        self.assertEqual(payload["events"][0]["seq"], 1)

    def test_clear_and_close(self) -> None:
        session = self.store.create_session(
            creator="Alice", participants=["Bob"], room="art"
        )
        token = session.tokens["Alice"]
        _, _, ticket, _ = self.store.issue_access_ticket(token, session.keys["Alice"])
        self.store.add_stroke(
            token, ticket, color="#000", width=2, points=[[1, 1], [2, 2]]
        )
        event, err = self.store.clear_board(token, ticket)
        self.assertEqual(err, "")
        self.assertEqual(event["kind"], "clear")
        payload, _ = self.store.sync_since(token, ticket, 0)
        self.assertEqual(len(payload["events"]), 1)
        self.assertEqual(payload["events"][0]["kind"], "clear")

        ok, err = self.store.close_session(session.session_id, "Bob")
        self.assertFalse(ok)
        ok, err = self.store.close_session(session.session_id, "Alice")
        self.assertTrue(ok)
        found = self.store.find_open_for_room("art")
        self.assertIsNone(found)

    def test_generate_canvas_page_contains_gate(self) -> None:
        import canvas_http

        page = canvas_http.generate_canvas_page("tok123", lang="zh")
        self.assertIn("访问密钥", page)
        self.assertIn("'/canvas/' + token + '/auth'", page)
        self.assertIn("X-Canvas-Ticket", page)


if __name__ == "__main__":
    unittest.main()
