"""Tests for shared canvas URL+key sessions and Excalidraw scene sync."""

from __future__ import annotations

import os
import tempfile
import time
import unittest

import canvas_sharing


def _el(eid: str, version: int, **extra):
    d = {
        "id": eid,
        "type": "rectangle",
        "x": 0,
        "y": 0,
        "width": 10,
        "height": 10,
        "version": version,
        "versionNonce": version * 10,
        "isDeleted": False,
    }
    d.update(extra)
    return d


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

    def test_key_never_needed_in_url_and_ticket_gates_scene(self) -> None:
        session = self.store.create_session(
            creator="Alice", participants=["Bob"], room="lab"
        )
        alice_token = session.tokens["Alice"]
        bob_token = session.tokens["Bob"]
        alice_key = session.keys["Alice"]

        s, p, t, err = self.store.issue_access_ticket(alice_token, "ZZZZZZ")
        self.assertIsNone(t)
        self.assertIn("密钥", err)

        s, p, ticket, err = self.store.issue_access_ticket(alice_token, alice_key)
        self.assertEqual(err, "")
        self.assertEqual(p, "Alice")
        self.assertTrue(ticket)

        result, err = self.store.apply_scene(
            alice_token,
            ticket,
            elements=[_el("a1", 1)],
        )
        self.assertEqual(err, "")
        self.assertEqual(result["rev"], 1)
        self.assertEqual(len(result["elements"]), 1)

        # Bob cannot use Alice's ticket on Bob's token
        bad, err = self.store.apply_scene(
            bob_token,
            ticket,
            elements=[_el("b1", 1)],
        )
        self.assertIsNone(bad)
        self.assertTrue(err)

        _, _, bob_ticket, err = self.store.issue_access_ticket(
            bob_token, session.keys["Bob"]
        )
        self.assertEqual(err, "")
        payload, err = self.store.sync_since(bob_token, bob_ticket, 0)
        self.assertEqual(err, "")
        self.assertTrue(payload["changed"])
        self.assertEqual(payload["rev"], 1)
        self.assertEqual(payload["elements"][0]["id"], "a1")

    def test_element_version_merge(self) -> None:
        session = self.store.create_session(
            creator="Alice", participants=["Bob"], room="merge"
        )
        at = session.tokens["Alice"]
        bt = session.tokens["Bob"]
        _, _, a_ticket, _ = self.store.issue_access_ticket(at, session.keys["Alice"])
        _, _, b_ticket, _ = self.store.issue_access_ticket(bt, session.keys["Bob"])

        self.store.apply_scene(at, a_ticket, elements=[_el("x", 1, width=10)])
        self.store.apply_scene(bt, b_ticket, elements=[_el("x", 2, width=99)])
        # Stale lower version must not win
        self.store.apply_scene(at, a_ticket, elements=[_el("x", 1, width=10)])
        payload, _ = self.store.sync_since(at, a_ticket, 0)
        self.assertEqual(payload["elements"][0]["width"], 99)
        self.assertEqual(payload["elements"][0]["version"], 2)

    def test_clear_and_close(self) -> None:
        session = self.store.create_session(
            creator="Alice", participants=["Bob"], room="art"
        )
        token = session.tokens["Alice"]
        _, _, ticket, _ = self.store.issue_access_ticket(token, session.keys["Alice"])
        self.store.apply_scene(token, ticket, elements=[_el("z", 1)])
        event, err = self.store.clear_board(token, ticket)
        self.assertEqual(err, "")
        self.assertEqual(event["kind"], "clear")
        payload, _ = self.store.sync_since(token, ticket, 0)
        self.assertEqual(payload["elements"], [])
        self.assertTrue(payload["rev"] >= 2)

        ok, err = self.store.close_session(session.session_id, "Bob")
        self.assertFalse(ok)
        ok, err = self.store.close_session(session.session_id, "Alice")
        self.assertTrue(ok)
        found = self.store.find_open_for_room("art")
        self.assertIsNone(found)

    def test_register_remote_session_mirror(self) -> None:
        session = self.store.register_remote_session(
            session_id="remote-sid",
            creator="Alice",
            participants=["Bob", "Carol"],
            room="fed",
            tokens={"Alice": "tok-a", "Bob": "tok-b"},
            keys={"Alice": "KEYAAA", "Bob": "KEYBBB"},
            host_node="node-b",
            host_base_url="https://cf.trycloudflare.com",
            expires=time.time() + 3600,
        )
        self.assertEqual(session.host_node, "node-b")
        self.assertEqual(session.host_base_url, "https://cf.trycloudflare.com")
        self.assertIsNone(self.store.get_by_token("tok-a"))
        found = self.store.find_open_for_room("fed")
        self.assertIsNotNone(found)
        self.assertEqual(found.session_id, "remote-sid")

    def test_reauth_keeps_recent_ticket_alive(self) -> None:
        session = self.store.create_session(
            creator="Alice", participants=["Bob"], room="lab"
        )
        token = session.tokens["Alice"]
        key = session.keys["Alice"]
        _, _, t1, err = self.store.issue_access_ticket(token, key)
        self.assertEqual(err, "")
        _, _, t2, err = self.store.issue_access_ticket(token, key)
        self.assertEqual(err, "")
        _, _, err1 = self.store.resolve_ticket(token, t1)
        _, _, err2 = self.store.resolve_ticket(token, t2)
        self.assertEqual(err1, "")
        self.assertEqual(err2, "")
        payload, err = self.store.sync_since(token, t1, 0)
        self.assertEqual(err, "")
        self.assertIsNotNone(payload)

    def test_sync_accepts_ticket_query_param(self) -> None:
        """HTTP layer: header optional when ?ticket= is present."""
        import canvas_http
        import canvas_sharing as cs

        session = self.store.create_session(
            creator="Alice", participants=[], room="q"
        )
        token = session.tokens["Alice"]
        key = session.keys["Alice"]
        _, _, ticket, _ = self.store.issue_access_ticket(token, key)
        self.store.apply_scene(token, ticket, elements=[_el("q1", 1)])

        class Fake:
            def __init__(self) -> None:
                self.path = f"/canvas/{token}/sync?since=0&ticket={ticket}"
                self.headers = {}
                self.code = None
                self.data = None

            def _send_error_json(self, code, msg):
                self.code, self.msg = code, msg

            def _send_json_response(self, code, data):
                self.code, self.data = code, data

        old = cs.canvas_store
        cs.canvas_store = self.store
        try:
            fake = Fake()
            self.assertTrue(canvas_http.handle_canvas_get(fake))  # type: ignore
            self.assertEqual(fake.code, 200)
            self.assertIsNotNone(fake.data)
            self.assertTrue(fake.data["changed"])
            self.assertEqual(fake.data["rev"], 1)
        finally:
            cs.canvas_store = old

    def test_generate_canvas_page_contains_gate_and_excalidraw(self) -> None:
        import canvas_http

        page = canvas_http.generate_canvas_page("tok123", lang="zh")
        self.assertIn("访问密钥", page)
        self.assertIn("'/canvas/' + token + '/auth'", page)
        self.assertIn("@excalidraw/excalidraw", page)
        self.assertIn("external=react,react-dom", page)
        self.assertIn("__SSHCHAT_KEY", page)
        self.assertIn("/scene", page)
        self.assertIn("X-Canvas-Ticket", page)
        self.assertIn("&ticket=", page)
        self.assertIn("cache: 'no-store'", page)
        self.assertIn("hashFragmentKey", page)
        self.assertIn("excalidraw-root", page)


if __name__ == "__main__":
    unittest.main()
