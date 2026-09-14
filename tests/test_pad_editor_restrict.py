""" /pad edit must not allow vim :term / :e / :vimgrep escapes by default. """

from __future__ import annotations

import os
import tempfile
import unittest

import client


class PadEditorRestrictTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.pop("SSHCHAT_PAD_UNRESTRICTED", None)
        self._pad = tempfile.NamedTemporaryFile(
            mode="w", suffix=".pad.txt", delete=False, encoding="utf-8"
        )
        self._pad.write("hello\n")
        self._pad.close()
        self._rc = client._write_pad_vim_lock_rc(self._pad.name)

    def tearDown(self) -> None:
        os.environ.pop("SSHCHAT_PAD_UNRESTRICTED", None)
        for p in (self._pad.name, self._rc):
            try:
                os.unlink(p)
            except OSError:
                pass

    def test_vim_gets_lock_rc_and_restricted_flag(self) -> None:
        argv = client._pad_editor_argv("vim", self._pad.name, self._rc)
        self.assertEqual(argv[0], "vim")
        self.assertIn("-Z", argv)
        self.assertIn("-u", argv)
        self.assertIn(self._rc, argv)
        self.assertIn("--noplugin", argv)

    def test_rvim_gets_lock_rc_without_extra_Z(self) -> None:
        argv = client._pad_editor_argv("rvim", self._pad.name, self._rc)
        self.assertEqual(argv[0], "rvim")
        self.assertNotIn("-Z", argv)
        self.assertIn(self._rc, argv)

    def test_nvim_blocks_termopen_and_uses_lock_rc(self) -> None:
        argv = client._pad_editor_argv("nvim", self._pad.name, self._rc)
        self.assertEqual(argv[0], "nvim")
        self.assertIn(self._rc, argv)
        self.assertTrue(any("termopen" in a for a in argv))

    def test_lock_rc_blocks_foreign_reads(self) -> None:
        other = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        )
        other.write("SECRET\n")
        other.close()
        try:
            import subprocess

            proc = subprocess.run(
                [
                    "vim",
                    "-Z",
                    "-u",
                    self._rc,
                    "-i",
                    "NONE",
                    "-n",
                    "--noplugin",
                    "-c",
                    f"try | e {other.name} | catch | echom v:exception | endtry",
                    "-c",
                    "qa!",
                    self._pad.name,
                ],
                capture_output=True,
                text=True,
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            self.assertIn("E145", out)
        finally:
            os.unlink(other.name)

    def test_unrestricted_escape_hatch(self) -> None:
        os.environ["SSHCHAT_PAD_UNRESTRICTED"] = "1"
        self.assertEqual(client._pad_editor_argv("vim", self._pad.name, self._rc), ["vim"])


if __name__ == "__main__":
    unittest.main()
