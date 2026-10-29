"""One-shot 5-minute guest chat links."""

from __future__ import annotations

import io
import unittest
from unittest import mock

import guest_http
import guest_sharing
import server


class DummyConn:
    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self._timeout = None

    def gettimeout(self):
        return self._timeout

    def settimeout(self, value) -> None:
        self._timeout = value

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def send(self, data: bytes) -> None:
        self.sent.append(data)

    def close(self) -> None:
        pass


class FakeHandler:
    def __init__(self, path: str, *, lang: str = "zh") -> None:
        self.path = path
        self.headers = {"Accept-Language": lang}
        self.status = 0
        self.sent_headers: list[tuple[str, str]] = []
        self.body = io.BytesIO()
        self.wfile = self.body

    def send_response(self, code, _message=None):
        self.status = code

    def send_header(self, key, value):
        self.sent_headers.append((key, value))

    def end_headers(self):
        pass

    def _read_json_body(self, limit: int = 64 * 1024) -> dict:
        return {"nick": "alice"}


class GuestStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        guest_sharing.guest_store = guest_sharing.GuestStore()

    def test_guest_conn_can_be_a_client_key(self) -> None:
        conn = guest_sharing.GuestConn()
        bucket = {conn: True}
        self.assertTrue(bucket[conn])
        rooms: set[object] = set()
        rooms.add(conn)
        self.assertIn(conn, rooms)

    def test_random_nick_cannot_be_a_linux_user(self) -> None:
        nick = guest_sharing.random_guest_nick()
        self.assertTrue(guest_sharing.is_guest_nick(nick))
        self.assertTrue(nick.startswith("~g"))
        self.assertEqual(len(nick), 8)
        self.assertNotRegex(nick, r"^[a-z_][a-z0-9_-]{0,31}$")

    def test_parse_minutes_default_and_cap(self) -> None:
        ok, err = guest_sharing.parse_minutes("10")
        self.assertEqual((ok, err), (10, ""))
        self.assertEqual(guest_sharing.parse_minutes("0")[1], "range")
        self.assertEqual(guest_sharing.parse_minutes("61")[1], "range")
        self.assertEqual(guest_sharing.parse_minutes("close")[1], "usage")

    def test_custom_minutes_sets_chat_ttl(self) -> None:
        link = guest_sharing.guest_store.create("Ada", "lobby", 12)
        assert link is not None
        self.assertEqual(link.minutes, 12)
        opened, err = guest_sharing.guest_store.open_once(link.token)
        self.assertEqual(err, "")
        assert opened is not None
        left = guest_sharing.guest_store.remaining(opened)
        self.assertGreater(left, 11 * 60)
        self.assertLessEqual(left, 12 * 60)

    def test_open_once_assigns_random_nick_and_second_fails(self) -> None:
        link = guest_sharing.guest_store.create("Ada", "lobby")
        assert link is not None
        first, err = guest_sharing.guest_store.open_once(link.token)
        self.assertEqual(err, "")
        assert first is not None
        self.assertTrue(guest_sharing.is_guest_nick(first.nick))
        second, err2 = guest_sharing.guest_store.open_once(link.token)
        self.assertIsNone(second)
        self.assertEqual(err2, "used")

    def test_refresh_get_fails_after_join(self) -> None:
        link = guest_sharing.guest_store.create("Ada", "lobby")
        assert link is not None
        guest_sharing.guest_store.open_once(link.token)
        handler = FakeHandler(f"/guest/{link.token}")
        self.assertTrue(guest_http.handle_guest_get(handler))
        self.assertEqual(handler.status, 410)
        self.assertIn("用过", handler.body.getvalue().decode("utf-8"))

    def test_join_ignores_client_supplied_nick(self) -> None:
        link = guest_sharing.guest_store.create("Ada", "lobby")
        assert link is not None
        handler = FakeHandler(f"/guest/{link.token}/join")
        self.assertTrue(guest_http.handle_guest_post(handler))
        self.assertEqual(handler.status, 200)
        self.assertNotEqual(link.nick, "~alice")
        self.assertNotIn("alice", link.nick.lower())
        self.assertTrue(guest_sharing.is_guest_nick(link.nick))


class GuestCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        guest_sharing.guest_store = guest_sharing.GuestStore()
        server.clients.clear()
        server.rooms.clear()
        server.room_owners.clear()
        self.alice = DummyConn()
        self.bob = DummyConn()
        server.clients[self.alice] = {
            "name": "Alice",
            "rooms": {"lobby"},
            "current_room": "lobby",
            "locale": "en",
        }
        server.clients[self.bob] = {
            "name": "Bob",
            "rooms": {"lobby"},
            "current_room": "lobby",
            "locale": "en",
        }
        server.rooms["lobby"] = {self.alice, self.bob}
        server.room_owners["lobby"] = self.alice

    def _out(self, conn: DummyConn) -> str:
        return b"".join(conn.sent).decode("utf-8")

    def test_bind_chat_uses_this_server_not_a_second_import(self) -> None:
        seen: list[str] = []

        def fake_attach(conn, name, room, locale="en"):
            seen.append(f"{name}@{room}")
            return name

        guest_http.bind_chat(
            attach_guest=fake_attach,
            process_client_line=server.process_client_line,
            remove_client=server.remove_client,
        )
        try:
            self.assertIs(guest_http._attach_guest, fake_attach)
            self.assertEqual(fake_attach(None, "~gabc123", "lobby"), "~gabc123")
            self.assertEqual(seen, ["~gabc123@lobby"])
        finally:
            guest_http.bind_chat(
                attach_guest=server.attach_guest,
                process_client_line=server.process_client_line,
                remove_client=server.remove_client,
            )

    def test_guest_minutes_arg(self) -> None:
        fake_http = mock.Mock()
        fake_http.get_base_url.return_value = "https://example.trycloudflare.com"
        with mock.patch.object(server, "file_http", fake_http):
            server.handle_command(self.alice, "/guest 10")
        self.assertIn("10 minutes", self._out(self.alice))
        self.alice.sent.clear()
        with mock.patch.object(server, "file_http", fake_http):
            server.handle_command(self.alice, "/guest 99")
        self.assertIn("1 to 60", self._out(self.alice))

    def test_link_is_private_and_not_gui_open(self) -> None:
        fake_http = mock.Mock()
        fake_http.get_base_url.return_value = "https://example.trycloudflare.com"
        with mock.patch.object(server, "file_http", fake_http):
            server.handle_command(self.alice, "/guest")
        alice = self._out(self.alice)
        self.assertIn("/guest/", alice)
        self.assertIn("only you", alice.lower())
        self.assertNotIn("gui-open", alice)
        self.assertEqual(self._out(self.bob), "")

    def test_attach_guest_conn_joins_room(self) -> None:
        gconn = guest_sharing.GuestConn()
        with mock.patch.object(server, "send_line"), mock.patch.object(
            server, "broadcast_room"
        ):
            nick = server.attach_guest(gconn, "alice", "lobby", "en")
        self.assertTrue(guest_sharing.is_guest_nick(nick))
        self.assertIn(gconn, server.clients)
        self.assertIn(gconn, server.rooms["lobby"])
        with mock.patch.object(gconn, "close"):
            server.remove_client(gconn)
        self.assertNotIn(gconn, server.clients)

    def test_guest_cannot_use_slash_commands_or_pick_nick(self) -> None:
        gconn = DummyConn()
        nick = server.attach_guest(gconn, "alice", "lobby", "en")
        self.assertTrue(guest_sharing.is_guest_nick(nick))
        self.assertNotEqual(nick.lower(), "alice")
        gconn.sent.clear()
        server.process_client_line(gconn, b"/help")
        self.assertIn("Guest links can only send chat text and images", self._out(gconn))
        gconn.sent.clear()
        server.process_client_line(gconn, b"hello from guest")
        self.assertIn("[#lobby] [" + nick + "] hello from guest", self._out(self.alice))

    def test_publish_guest_image_notifies_room(self) -> None:
        # Minimal 1x1 PNG
        png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00"
            b"\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18"
            b"\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        gconn = DummyConn()
        nick = server.attach_guest(gconn, "~gabcdef", "lobby", "en")
        fake_http = mock.Mock()
        fake_http.get_base_url.return_value = "https://example.trycloudflare.com"
        with mock.patch.object(server, "file_http", fake_http):
            ok, err = server.publish_guest_image(nick, "lobby", "hi.png", png)
        self.assertTrue(ok, err)
        self.assertEqual(err, "")
        # Upload-complete callback is async; mark_upload_complete already ran.
        # Recipients should include Alice/Bob once notify fires — poll store.
        store = __import__("file_sharing", fromlist=["file_transfer_store"]).file_transfer_store
        found = [
            t
            for t in store.transfers.values()
            if t.sender == nick and t.upload_used and t.filename.endswith(".png")
        ]
        self.assertTrue(found)
        self.assertIn("Alice", found[0].download_tokens)
        self.assertIn("Bob", found[0].download_tokens)

    def test_looks_like_image_rejects_text(self) -> None:
        self.assertFalse(guest_sharing.looks_like_image(b"hello", "x.txt"))
        self.assertTrue(guest_sharing.looks_like_image(b"\xff\xd8\xff\xe0", "x.bin"))


class GuestPageTests(unittest.TestCase):
    def test_landing_has_countdown_and_no_nick_box(self) -> None:
        guest_sharing.guest_store = guest_sharing.GuestStore()
        link = guest_sharing.guest_store.create("Ada", "lobby")
        assert link is not None
        page = guest_http.render_guest_page(link, "zh")
        self.assertIn("请勿刷新", page)
        self.assertIn("倒计时", page)
        self.assertIn("分", page)
        self.assertIn("秒", page)
        self.assertNotIn('id="nick"', page)
        self.assertIn("/guest/", page)
        self.assertIn('id="pick"', page)
        self.assertIn("/guest/' + boot.token + '/image", page)
        self.assertIn("可发文字和图片", page)


if __name__ == "__main__":
    unittest.main()
