"""Music drop-folder catalog, path safety, Range serving, gui-open invite."""
from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import music
import music_http
import server


class _FakeHandler:
    def __init__(self, path: str, range_header: str = "", command: str = "GET"):
        self.path = path
        self.command = command
        self.headers = {"Range": range_header} if range_header else {}
        self.status = None
        self.sent_headers = []
        self.body = io.BytesIO()
        self.wfile = self.body

    def send_response(self, code):
        self.status = code

    def send_header(self, key, value):
        self.sent_headers.append((key, value))

    def end_headers(self):
        pass

    def _send_error_json(self, code, _msg):
        self.status = code

    def _send_html_error(self, code, _msg, lang="en"):
        self.status = code

    def _send_html_page(self, markup):
        self.status = 200
        self.body.write(markup.encode("utf-8"))

    def _read_json_body(self):
        return getattr(self, "json_body", {})

    def _send_json_response(self, code, data):
        self.status = code
        self.json_out = data


class MusicCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "b.wav").write_bytes(b"RIFF" + b"\x00" * 12)
        (self.dir / "a.mp3").write_bytes(b"ID3" + b"\x00" * 20)
        (self.dir / "notes.txt").write_text("nope")
        (self.dir / "sub").mkdir()
        (self.dir / "sub" / "hidden.mp3").write_bytes(b"xx")
        self.addCleanup(self.tmp.cleanup)

    def test_list_sorted_audio_only(self):
        songs = music.list_songs(self.dir)
        self.assertEqual([s.name for s in songs], ["a.mp3", "b.wav"])
        self.assertEqual(songs[0].index, 1)
        self.assertEqual(songs[1].ext, "wav")

    def test_resolve_and_find(self):
        catalog = music.list_songs(self.dir)
        self.assertEqual(music.resolve_song("2", catalog).name, "b.wav")
        self.assertEqual(music.resolve_song("a.mp3", catalog).name, "a.mp3")
        self.assertIsNone(music.resolve_song("../a.mp3", catalog))
        self.assertEqual(len(music.search_catalog(catalog, "wav")), 1)

    def test_path_escape_rejected(self):
        self.assertIsNone(music.resolve_song_file(self.dir, "../a.mp3"))
        self.assertIsNone(music.resolve_song_file(self.dir, "sub/hidden.mp3"))
        self.assertIsNone(music.resolve_song_file(self.dir, "notes.txt"))
        got = music.resolve_song_file(self.dir, "a.mp3")
        self.assertEqual(got.name, "a.mp3")


class MusicHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        payload = b"ABCDEFGHIJ" * 100
        (self.dir / "track.mp3").write_bytes(payload)
        self.payload = payload
        self.prev = os.environ.get("SSHCHAT_MUSIC_DIR")
        os.environ["SSHCHAT_MUSIC_DIR"] = str(self.dir)
        self.addCleanup(self._restore)

    def _restore(self):
        self.tmp.cleanup()
        if self.prev is None:
            os.environ.pop("SSHCHAT_MUSIC_DIR", None)
        else:
            os.environ["SSHCHAT_MUSIC_DIR"] = self.prev

    def test_page_has_player_and_range_unlocks(self):
        sess = music.music_store.mint(1, "ada")
        handler = _FakeHandler(f"/music/{sess.token}")
        self.assertTrue(music_http.handle_music_get(handler))
        html = handler.body.getvalue().decode("utf-8")
        self.assertIn("<audio", html)
        self.assertIn("track.mp3", html)
        self.assertIn('id="closeBtn"', html)
        self.assertIn('id="key"', html)
        self.assertIn('id="unlockBtn"', html)
        self.assertIn('id="lock"', html)
        self.assertIn('id="stageWrap"', html)
        self.assertLess(html.find('id="closeBtn"'), html.find('id="lock"'))
        self.assertIn("/auth", html)
        self.assertIn("decodeAudioData", html)
        self.assertIn("location.origin", html)
        self.assertIn(sess.token, html)

        bad = _FakeHandler(f"/music/{sess.token}/auth")
        bad.json_body = {"key": "XXXXXX"}
        self.assertTrue(music_http.handle_music_post(bad))
        self.assertEqual(bad.status, 403)

        ok = _FakeHandler(f"/music/{sess.token}/auth")
        ok.json_body = {"key": sess.key}
        self.assertTrue(music_http.handle_music_post(ok))
        self.assertEqual(ok.status, 200)

        full = _FakeHandler(f"/music/{sess.token}/file/track.mp3")
        music_http.handle_music_get(full)
        self.assertEqual(full.status, 200)
        self.assertEqual(full.body.getvalue(), self.payload)
        self.assertIn(("Content-Type", "audio/mpeg"), full.sent_headers)
        self.assertIn(("Content-Length", "1000"), full.sent_headers)

    def test_invite_includes_gui_open_music(self):
        song = music.SongEntry(1, "a.mp3", "mp3", 12, Path("a.mp3"))
        fake_http = mock.Mock()
        fake_http.get_base_url.return_value = "https://example.trycloudflare.com"
        with mock.patch.object(server, "file_http", fake_http):
            sess = music.music_store.mint(1, "ada")
            url = f"https://example.trycloudflare.com/music/{sess.token}"
            text = server._music_invite_message(
                url=url, key=sess.key, song=song, lang="zh"
            )
        self.assertIn(f"gui-open music {url} {sess.key}", text)
        self.assertIn("点歌", text)


class MusicGuiOpenTests(unittest.TestCase):
    def test_gui_open_music_re(self):
        import sshchat_gui as gui

        url = "https://abc.trycloudflare.com/music/tok123"
        m = gui._GUI_OPEN_MUSIC_RE.match(f"gui-open music {url} ABC123")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), url)
        self.assertEqual(m.group(2), "ABC123")
        self.assertIsNone(gui._GUI_OPEN_MUSIC_RE.match(f"gui-open music {url}"))


if __name__ == "__main__":
    unittest.main()
