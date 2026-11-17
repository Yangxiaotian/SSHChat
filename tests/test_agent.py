""" /agent — guest-style link bridged to local Cursor agent CLI. """

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import agent_bridge
import guest_http
import guest_sharing
import server


class FakeSock:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def gettimeout(self):
        return None

    def settimeout(self, value) -> None:
        pass

    def close(self) -> None:
        pass


class AgentCommandTests(unittest.TestCase):
    AGENT_PW = "test-secret"

    def setUp(self) -> None:
        guest_sharing.guest_store = guest_sharing.GuestStore()
        server.clients.clear()
        server.rooms.clear()
        server.rooms[server.DEFAULT_ROOM] = set()
        self.alice = FakeSock()
        self._tmpdir = tempfile.mkdtemp(prefix="sshchat-agent-test-")
        self._env = mock.patch.dict(
            os.environ,
            {
                "SSHCHAT_AGENT_TMP": self._tmpdir,
                "SSHCHAT_AGENT_PASSWORD": self.AGENT_PW,
            },
        )
        self._env.start()
        agent_bridge.reset_agent_password_attempts()
        with mock.patch.object(server, "send_line"), mock.patch.object(
            server, "broadcast_room"
        ):
            server.clients[self.alice] = {
                "name": "alice",
                "current_room": "lobby",
                "rooms": {"lobby"},
                "locale": "en",
            }
            server.rooms["lobby"] = {self.alice}

    def tearDown(self) -> None:
        self._env.stop()
        agent_bridge.reset_agent_password_attempts()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_agent_create_sets_per_link_workspace(self) -> None:
        lines: list[str] = []

        def capture(conn, text: str) -> None:
            lines.append(text)

        fake_http = mock.Mock()
        fake_http.get_base_url.return_value = "https://example.trycloudflare.com"
        with mock.patch.object(server, "file_http", fake_http), mock.patch.object(
            server, "send_line", side_effect=capture
        ), mock.patch(
            "agent_bridge.agent_bin", return_value="/usr/bin/agent"
        ):
            server.handle_command(self.alice, f"/agent {self.AGENT_PW} 10")
        links = list(guest_sharing.guest_store.links.values())
        self.assertEqual(len(links), 1)
        ws = links[0].agent_workspace
        self.assertTrue(ws.startswith(self._tmpdir))
        self.assertEqual(Path(ws).parent.name, "lobby")
        self.assertEqual(
            Path(ws).name, agent_bridge.safe_token_slug(links[0].token)
        )
        self.assertTrue(Path(ws, "CONTEXT.md").is_file())

    def test_two_links_do_not_share_workspace(self) -> None:
        a = guest_sharing.guest_store.create("alice", "wo", 5, kind="agent")
        b = guest_sharing.guest_store.create("alice", "wo", 5, kind="agent")
        assert a and b
        wa = agent_bridge.reset_workspace("wo", a.token)
        Path(wa, "uploads", "a.bin").write_bytes(b"a")
        wb = agent_bridge.reset_workspace("wo", b.token)
        self.assertNotEqual(wa, wb)
        self.assertTrue(Path(wa, "uploads", "a.bin").is_file())
        self.assertFalse(Path(wb, "uploads", "a.bin").exists())

    def test_parse_bot_address(self) -> None:
        hit, cleaned = agent_bridge.parse_bot_address("hello")
        self.assertFalse(hit)
        hit, cleaned = agent_bridge.parse_bot_address("@bot 本机什么配置")
        self.assertTrue(hit)
        self.assertEqual(cleaned, "本机什么配置")
        hit, cleaned = agent_bridge.parse_bot_address("请 ＠bot 看看")
        self.assertTrue(hit)
        self.assertIn("看看", cleaned)

    def test_turn_prompt_marks_stripped_at_bot(self) -> None:
        """Server strips @bot before turn(); prompts must not say 'no @bot'."""
        self.assertIn("stripped", agent_bridge._TURN_PREFIX_ASK.lower())
        self.assertIn("stripped", agent_bridge._TURN_PREFIX_FULL.lower())
        self.assertNotIn(
            "without @bot",
            agent_bridge._TURN_PREFIX_ASK.lower()
            + agent_bridge._TURN_PREFIX_FULL.lower(),
        )
        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured["prompt"] = cmd[-1]
            return mock.Mock(stdout='{"type":"result","result":"ok"}\n', stderr="", returncode=0)

        with mock.patch.object(agent_bridge, "agent_bin", return_value="/usr/bin/agent"), mock.patch(
            "subprocess.run", side_effect=fake_run
        ):
            agent_bridge.turn(
                "yxt: 帮我提交推送 SSHChat",
                workspace_dir=self._tmpdir,
                mode="ask",
            )
        self.assertIn("stripped", captured["prompt"].lower())
        self.assertIn("帮我提交推送", captured["prompt"])
        # Resume path too.
        captured.clear()
        with mock.patch.object(agent_bridge, "agent_bin", return_value="/usr/bin/agent"), mock.patch(
            "subprocess.run", side_effect=fake_run
        ):
            agent_bridge.turn(
                "yxt: again",
                session_id="sess1",
                workspace_dir=self._tmpdir,
                mode="full",
            )
        self.assertIn("stripped", captured["prompt"].lower())
        self.assertIn("FULL", captured["prompt"])

    def test_find_live_agent_before_open(self) -> None:
        link = guest_sharing.guest_store.create("alice", "lobby", 5, kind="agent")
        assert link is not None
        self.assertEqual(link.opened_at, 0.0)
        self.assertIs(guest_sharing.guest_store.find_live_agent("lobby"), link)

    def test_room_at_bot_routes_to_live_agent(self) -> None:
        link = guest_sharing.guest_store.create("alice", "lobby", 5, kind="agent")
        assert link is not None
        # Not opened yet — @bot must still route after /agent create.
        link.agent_workspace = agent_bridge.reset_workspace("lobby", link.token)
        seen: list[str] = []

        def capture(room, data: bytes, exclude_conn=None) -> None:
            seen.append(data.decode("utf-8", errors="replace"))

        with mock.patch.object(server, "broadcast_room", side_effect=capture), mock.patch(
            "guest_http._schedule_agent_reply"
        ) as sched:
            server.process_client_line(self.alice, b"@bot what is this host\n")
        sched.assert_called_once()
        args = sched.call_args[0]
        self.assertIs(args[0], link)
        self.assertIn("alice:", args[1])
        self.assertIn("what is this host", args[1])
        # plain chat does not route
        sched.reset_mock()
        with mock.patch.object(server, "broadcast_room", side_effect=capture), mock.patch(
            "guest_http._schedule_agent_reply"
        ) as sched2:
            server.process_client_line(self.alice, b"hello humans\n")
        sched2.assert_not_called()

    def test_room_member_at_bot_allowed(self) -> None:
        link = guest_sharing.guest_store.create("alice", "lobby", 5, kind="agent")
        assert link is not None
        bob = FakeSock()
        server.clients[bob] = {
            "name": "bob",
            "current_room": "lobby",
            "rooms": {"lobby"},
            "locale": "en",
        }
        server.rooms["lobby"].add(bob)
        with mock.patch.object(server, "broadcast_room"), mock.patch(
            "guest_http._schedule_agent_reply"
        ) as sched:
            server.process_client_line(bob, b"@bot hack the planet\n")
        sched.assert_called_once()
        self.assertIn("bob:", sched.call_args[0][1])

    def test_agent_create_requires_password(self) -> None:
        lines: list[str] = []

        def capture(conn, text: str) -> None:
            lines.append(text)

        fake_http = mock.Mock()
        fake_http.get_base_url.return_value = "https://example.trycloudflare.com"
        with mock.patch.object(server, "file_http", fake_http), mock.patch.object(
            server, "send_line", side_effect=capture
        ), mock.patch("agent_bridge.agent_bin", return_value="/usr/bin/agent"):
            server.handle_command(self.alice, "/agent")
            server.handle_command(self.alice, "/agent wrong-pw")
        self.assertTrue(any("Usage" in x or "用法" in x for x in lines))
        self.assertTrue(any("Wrong" in x or "错误" in x for x in lines))
        self.assertIsNone(guest_sharing.guest_store.find_live_agent("lobby"))

    def test_agent_password_lockout(self) -> None:
        for _ in range(5):
            code, _ = agent_bridge.check_agent_password("alice", "nope")
            self.assertEqual(code, "bad")
        code, kw = agent_bridge.check_agent_password("alice", "nope")
        self.assertEqual(code, "locked")
        self.assertGreaterEqual(int(kw.get("seconds") or 0), 60)
        code, _ = agent_bridge.check_agent_password("alice", self.AGENT_PW)
        self.assertEqual(code, "locked")

    def test_ask_cli_json_has_no_shell(self) -> None:
        ws = agent_bridge.reset_workspace("lobby", "tok", mode="ask")
        raw = Path(ws, ".cursor", "cli.json").read_text(encoding="utf-8")
        data = json.loads(raw)
        allow = " ".join(data["permissions"]["allow"])
        self.assertNotIn("Shell(", allow)
        self.assertIn("Read(**)", allow)
        deny = " ".join(data["permissions"]["deny"])
        self.assertIn("Shell(sudo)", deny)

    def test_extra_add_dirs_includes_home_by_default(self) -> None:
        home = tempfile.mkdtemp(prefix="sshchat-agent-home-")
        try:
            with mock.patch.dict(
                os.environ,
                {"SSHCHAT_AGENT_HOME": home, "SSHCHAT_AGENT_ADD_HOME": "1"},
                clear=False,
            ):
                dirs = agent_bridge.extra_add_dirs()
            self.assertIn(os.path.realpath(home), dirs)
            with mock.patch.dict(
                os.environ,
                {"SSHCHAT_AGENT_HOME": home, "SSHCHAT_AGENT_ADD_HOME": "0"},
                clear=False,
            ):
                dirs_off = agent_bridge.extra_add_dirs()
            self.assertNotIn(os.path.realpath(home), dirs_off)
            with mock.patch.dict(
                os.environ,
                {"SSHCHAT_AGENT_HOME": home, "SSHCHAT_AGENT_ADD_HOME": "1"},
                clear=False,
            ):
                full = json.loads(
                    agent_bridge.cli_json_for_mode("full", workspace_dir="/tmp/ws")
                )
            self.assertTrue(
                any(
                    home in x
                    for x in full["permissions"]["allow"]
                    if x.startswith("Write(")
                )
            )
        finally:
            shutil.rmtree(home, ignore_errors=True)

    def test_redact_secrets(self) -> None:
        out = agent_bridge.redact_secrets("key=crsr_da6885802186fb0ba5e3b182ca9a0c99")
        self.assertIn("[redacted]", out)
        self.assertNotIn("crsr_da6885802186fb0ba5e3b182ca9a0c99", out)

    def test_mode_full_needs_allow(self) -> None:
        fake_http = mock.Mock()
        fake_http.get_base_url.return_value = "https://example.trycloudflare.com"
        lines: list[str] = []

        def capture(conn, text: str) -> None:
            lines.append(text)

        with mock.patch.object(server, "file_http", fake_http), mock.patch.object(
            server, "send_line", side_effect=capture
        ), mock.patch.object(server, "broadcast_room"), mock.patch(
            "agent_bridge.agent_bin", return_value="/usr/bin/agent"
        ):
            server.handle_command(self.alice, f"/agent {self.AGENT_PW}")
            link = guest_sharing.guest_store.find_live_agent("lobby")
            assert link is not None
            self.assertEqual(link.agent_mode, "ask")
            server.handle_command(self.alice, "/agent mode full")
            self.assertEqual(link.agent_mode, "full")
            self.assertFalse(link.agent_full_allowed)
            self.assertEqual(guest_http._effective_agent_mode(link), "ask")
            server.handle_command(self.alice, "/agent allow")
            self.assertTrue(link.agent_full_allowed)
            self.assertEqual(guest_http._effective_agent_mode(link), "full")

    def test_agent_full_alias_not_password(self) -> None:
        fake_http = mock.Mock()
        fake_http.get_base_url.return_value = "https://example.trycloudflare.com"
        lines: list[str] = []

        def capture(conn, text: str) -> None:
            lines.append(text)

        with mock.patch.object(server, "file_http", fake_http), mock.patch.object(
            server, "send_line", side_effect=capture
        ), mock.patch.object(server, "broadcast_room"), mock.patch(
            "agent_bridge.agent_bin", return_value="/usr/bin/agent"
        ):
            server.handle_command(self.alice, f"/agent {self.AGENT_PW} 15")
            link = guest_sharing.guest_store.find_live_agent("lobby")
            assert link is not None
            lines.clear()
            server.handle_command(self.alice, "/agent full")
            self.assertEqual(link.agent_mode, "full")
            self.assertFalse(link.agent_full_allowed)
            self.assertTrue(
                any("Full mode" in x or "full" in x.lower() for x in lines)
            )
            # Must not count as a wrong-password attempt.
            code, _ = agent_bridge.check_agent_password("alice", self.AGENT_PW)
            self.assertEqual(code, "ok")
            lines.clear()
            server.handle_command(self.alice, "/agent something-else")
            self.assertTrue(
                any("already" in x.lower() or "已在运行" in x for x in lines)
            )
            code, _ = agent_bridge.check_agent_password("alice", self.AGENT_PW)
            self.assertEqual(code, "ok")

    def test_agent_create_announces_online(self) -> None:
        lines: list[str] = []
        room_lines: list[str] = []

        def capture(conn, text: str) -> None:
            lines.append(text)

        def fanout(room, data: bytes, exclude_conn=None) -> None:
            room_lines.append(data.decode("utf-8", errors="replace"))

        fake_http = mock.Mock()
        fake_http.get_base_url.return_value = "https://example.trycloudflare.com"
        with mock.patch.object(server, "file_http", fake_http), mock.patch.object(
            server, "send_line", side_effect=capture
        ), mock.patch.object(server, "broadcast_room", side_effect=fanout), mock.patch(
            "agent_bridge.agent_bin", return_value="/usr/bin/agent"
        ), mock.patch(
            "agent_bridge.reset_workspace", return_value="/tmp/sshchat-agent/lobby/x"
        ):
            server.handle_command(self.alice, f"/agent {self.AGENT_PW}")
        self.assertTrue(any("@bot" in x or "上线" in x or "online" in x.lower() for x in room_lines))
        link = guest_sharing.guest_store.find_live_agent("lobby")
        self.assertIsNotNone(link)
        self.assertEqual(getattr(link, "opened_at", 0), 0)

    def test_agent_missing_cli(self) -> None:
        lines: list[str] = []

        def capture(conn, text: str) -> None:
            lines.append(text)

        fake_http = mock.Mock()
        fake_http.get_base_url.return_value = "https://example.trycloudflare.com"
        with mock.patch.object(server, "file_http", fake_http), mock.patch.object(
            server, "send_line", side_effect=capture
        ), mock.patch("agent_bridge.agent_bin", return_value=None):
            server.handle_command(self.alice, f"/agent {self.AGENT_PW}")
        self.assertTrue(
            any("agent" in x.lower() and "not found" in x.lower() for x in lines)
            or any("CLI" in x for x in lines)
        )

    def test_agent_close_only_agent_links(self) -> None:
        guest_sharing.guest_store.create("alice", "lobby", 5, kind="guest")
        guest_sharing.guest_store.create("alice", "lobby", 5, kind="agent")
        lines: list[str] = []

        def capture(conn, text: str) -> None:
            lines.append(text)

        fake_http = mock.Mock()
        fake_http.get_base_url.return_value = "https://example.trycloudflare.com"
        with mock.patch.object(server, "file_http", fake_http), mock.patch.object(
            server, "send_line", side_effect=capture
        ), mock.patch("agent_bridge.agent_bin", return_value="/usr/bin/agent"):
            server.handle_command(self.alice, "/agent close")
        open_kinds = [
            link.kind
            for link in guest_sharing.guest_store.links.values()
            if not link.closed
        ]
        self.assertEqual(open_kinds, ["guest"])

    def test_schedule_agent_reply_private(self) -> None:
        link = guest_sharing.guest_store.create("alice", "lobby", 5, kind="agent")
        assert link is not None
        link.opened_at = 1.0
        link.chat_expires_at = 1e18
        link.locale = "en"
        link.agent_workspace = agent_bridge.reset_workspace("lobby", link.token)
        seen: list[bytes] = []
        ws_msgs: list[dict] = []
        private: list[str] = []

        class FakeConn:
            def send_json(self, obj: dict) -> bool:
                ws_msgs.append(obj)
                return True

        link.conn = FakeConn()

        def fanout(room: str, data: bytes, exclude_conn=None) -> None:
            seen.append(data)

        def deliver(lnk, text: str) -> None:
            private.append(text)
            lnk.agent_last_reply = text

        guest_http.bind_chat(
            attach_guest=server.attach_guest,
            process_client_line=server.process_client_line,
            remove_client=server.remove_client,
            broadcast_room=fanout,
            deliver_agent_private=deliver,
        )
        with mock.patch(
            "agent_bridge.turn",
            return_value=("hello **bold**\nand two", "sess-1"),
        ):
            guest_http._schedule_agent_reply(link, "hi")
            import time

            for _ in range(80):
                if any(m.get("t") == "md" for m in ws_msgs):
                    break
                time.sleep(0.02)
        self.assertTrue(any(m.get("phase") == "thinking" for m in ws_msgs))
        md = next(m for m in ws_msgs if m.get("t") == "md")
        self.assertIn("hello **bold**", md["s"])
        self.assertTrue(private)
        self.assertIn("hello **bold**", private[0])
        # Room must not get the full private body.
        self.assertFalse(any(b"hello **bold**" in x for x in seen))
        self.assertEqual(link.agent_session, "sess-1")
        self.assertEqual(link.agent_last_reply, private[0])

    def test_agent_page_mentions_at_bot(self) -> None:
        link = guest_sharing.guest_store.create("alice", "lobby", 5, kind="agent")
        assert link is not None
        page = guest_http.render_guest_page(link, "zh")
        self.assertIn("@bot", page)
        self.assertIn('id="pickFile"', page)
        self.assertIn("<textarea", page)
        self.assertIn("交图", page)
        self.assertIn("交音", page)
        self.assertIn("交文件", page)
        self.assertIn("不是房间传文件", page)
        self.assertIn('id="composer"', page)
        self.assertIn("function renderMd", page)
        self.assertIn("startHeartbeat", page)
        self.assertIn("正在思考", page)

    def test_busy_queues_latest_pending(self) -> None:
        link = guest_sharing.guest_store.create("alice", "lobby", 5, kind="agent")
        assert link is not None
        link.agent_busy = True
        guest_http._schedule_agent_reply(link, "first")
        guest_http._schedule_agent_reply(link, "本机什么配置")
        self.assertEqual(link.agent_pending, "本机什么配置")

    def test_agent_ingest_skips_room_file_channel(self) -> None:
        link = guest_sharing.guest_store.create("alice", "lobby", 5, kind="agent")
        assert link is not None
        link.opened_at = 1.0
        link.chat_expires_at = 1e18
        link.nick = "~gtest"
        link.agent_workspace = agent_bridge.reset_workspace("lobby", link.token)
        notices: list[bytes] = []

        def fanout(room: str, data: bytes) -> None:
            notices.append(data)

        guest_http.bind_chat(
            attach_guest=server.attach_guest,
            process_client_line=server.process_client_line,
            remove_client=server.remove_client,
            broadcast_room=fanout,
        )
        with mock.patch("guest_http._schedule_agent_reply") as sched, mock.patch(
            "guest_http._publish_guest_image"
        ) as pub:
            path = guest_http._agent_ingest_upload(
                link, "shot.png", b"\x89PNG\r\n\x1a\n", kind="image"
            )
        pub.assert_not_called()
        self.assertTrue(path.startswith(link.agent_workspace))
        self.assertTrue(any(b"~agent" in n and b"shot" in n for n in notices) or any(
            b"sent image" in n for n in notices
        ))
        sched.assert_called_once()

    def test_turn_uses_link_workspace(self) -> None:
        seen: list[list[str]] = []

        run_kwargs: list[dict] = []

        def fake_run(cmd, **kwargs):
            seen.append(list(cmd))
            run_kwargs.append(dict(kwargs))

            class P:
                returncode = 0
                stdout = json.dumps(
                    {
                        "type": "result",
                        "result": "ok",
                        "session_id": "s1",
                        "is_error": False,
                    }
                )
                stderr = ""

            return P()

        ws = agent_bridge.reset_workspace("wo", "tok123", mode="ask")
        with mock.patch("agent_bridge.agent_bin", return_value="/usr/bin/agent"), mock.patch(
            "agent_bridge.subprocess.run", side_effect=fake_run
        ):
            agent_bridge.turn("hi", workspace_dir=ws, mode="ask")
        self.assertEqual(seen[0][seen[0].index("--workspace") + 1], ws)
        self.assertIn("--mode", seen[0])
        self.assertIn("ask", seen[0])
        self.assertNotIn("--yolo", seen[0])
        self.assertIn("@bot", seen[0][-1])
        self.assertTrue(run_kwargs[0].get("start_new_session"))


if __name__ == "__main__":
    unittest.main()
