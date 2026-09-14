""" /pad edit must not allow vim :term / :! shell escapes by default. """

from __future__ import annotations

import os
import unittest

import client


class PadEditorRestrictTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.pop("SSHCHAT_PAD_UNRESTRICTED", None)

    def tearDown(self) -> None:
        os.environ.pop("SSHCHAT_PAD_UNRESTRICTED", None)

    def test_vim_gets_restricted_flag(self) -> None:
        self.assertEqual(client._pad_editor_argv("vim")[:2], ["vim", "-Z"])
        self.assertEqual(client._pad_editor_argv("vim -n"), ["vim", "-Z", "-n"])
        self.assertEqual(client._pad_editor_argv("vim -Z -n"), ["vim", "-Z", "-n"])
        self.assertEqual(client._pad_editor_argv("rvim"), ["rvim"])

    def test_nvim_blocks_termopen(self) -> None:
        argv = client._pad_editor_argv("nvim")
        self.assertEqual(argv[0], "nvim")
        self.assertTrue(any("termopen" in a for a in argv))
        self.assertTrue(any(a.startswith("set shell=") for a in argv))

    def test_unrestricted_escape_hatch(self) -> None:
        os.environ["SSHCHAT_PAD_UNRESTRICTED"] = "1"
        self.assertEqual(client._pad_editor_argv("vim"), ["vim"])


if __name__ == "__main__":
    unittest.main()
