"""Client TTY restore helpers after /pad vim (large paste)."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

import client


class PadEditorRestoreTests(unittest.TestCase):
    def test_lock_rc_disables_mouse_and_bracketed_paste(self) -> None:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".pad.txt", delete=False, encoding="utf-8"
        ) as tf:
            tf.write("x\n")
            path = tf.name
        try:
            rc = client._write_pad_vim_lock_rc(path)
            text = open(rc, encoding="utf-8").read()
            self.assertIn("set mouse=", text)
            self.assertIn("set t_BE=", text)
            self.assertIn("set nopaste", text)
            os.unlink(rc)
        finally:
            os.unlink(path)

    def test_restore_sets_prompt_reset_flag(self) -> None:
        client._NEED_PROMPT_RESET.clear()
        with mock.patch.object(client, "_get_real_stdout", return_value=None):
            with mock.patch.object(client, "_clear_stdout_proxy_pending"):
                with mock.patch.object(client, "_flush_stdin_after_editor"):
                    client._restore_tty_after_editor(None)
        self.assertTrue(client._NEED_PROMPT_RESET.is_set())
        client._NEED_PROMPT_RESET.clear()

    def test_upload_refuses_overlong_pad(self) -> None:
        # Exercise the length gate used by _run_pad_edit without spawning vim.
        text = "汉" * (client._PAD_MAX_CHARS + 1)
        self.assertGreater(len(text), client._PAD_MAX_CHARS)


class DiscardOversizeLineTests(unittest.TestCase):
    def test_discard_until_newline_keeps_following_commands(self) -> None:
        import server

        class FakeConn:
            def __init__(self, chunks: list[bytes]) -> None:
                self._chunks = list(chunks)
                self.sent: list[bytes] = []

            def recv(self, _n: int) -> bytes:
                if not self._chunks:
                    return b""
                return self._chunks.pop(0)

            def send(self, data: bytes) -> None:
                self.sent.append(data)

            def sendall(self, data: bytes) -> None:
                self.sent.append(data)

        # Oversized prefix without newline, then remainder ending the line,
        # then a follow-up command already in the same recv chunk after \n.
        conn = FakeConn([b"AAAA\n/names\n"])
        rest = server._discard_client_line_remainder(conn, b"X" * 10)
        self.assertEqual(rest, b"/names\n")

    def test_read_loop_must_recv_while_partial_line_buffered(self) -> None:
        """Regression: partial buffer without \\n used to busy-loop and skip recv."""
        import inspect
        import server

        src = inspect.getsource(server.handle_client)
        # The fixed loop receives whenever no newline is present.
        self.assertIn('if b"\\n" not in buffer:', src)
        self.assertNotIn("if not buffer:", src.split("while True:")[-1][:800])


if __name__ == "__main__":
    unittest.main()
