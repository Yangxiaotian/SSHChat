#!/usr/bin/env python3
"""FileHTTP /healthz for Cloudflare tunnel probes."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

tmpdir = tempfile.mkdtemp(prefix="sshchat_healthz_")
os.environ["SSHCHAT_FILE_STORAGE_DIR"] = os.path.join(tmpdir, "files")
os.environ["SSHCHAT_FILE_TRANSFER_STORE"] = os.path.join(tmpdir, "transfers.json")

import file_http_server  # noqa: E402


class FileHttpHealthzTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.port = 18447
        cls.srv = file_http_server.FileHTTPServer(
            host="127.0.0.1", port=cls.port, use_https=False
        )
        cls.srv.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.srv.stop()
        except Exception:
            pass

    def test_healthz_ok(self) -> None:
        with urllib.request.urlopen(self.base + "/healthz", timeout=3) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.read(), b"ok\n")

    def test_health_alias_ok(self) -> None:
        with urllib.request.urlopen(self.base + "/health", timeout=3) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.read(), b"ok\n")


if __name__ == "__main__":
    unittest.main()
