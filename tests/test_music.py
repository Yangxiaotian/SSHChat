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
        (self.dir / "星际穿越(Cornfield Chase).mp3").write_bytes(payload)
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
        self.assertTrue(ok.json_out.get("ok"))
        names = [s["name"] for s in ok.json_out.get("songs") or []]
        self.assertIn("track.mp3", names)

        full = _FakeHandler(f"/music/{sess.token}/file/track.mp3")
        music_http.handle_music_get(full)
        self.assertEqual(full.status, 200)
        self.assertEqual(full.body.getvalue(), self.payload)
        self.assertIn(("Content-Type", "audio/mpeg"), full.sent_headers)
        self.assertIn(("Content-Length", "1000"), full.sent_headers)

        from urllib.parse import quote

        cjk = "星际穿越(Cornfield Chase).mp3"
        encoded = _FakeHandler(
            f"/music/{sess.token}/file/{quote(cjk, safe='')}"
        )
        music_http.handle_music_get(encoded)
        self.assertEqual(encoded.status, 200)
        self.assertEqual(encoded.body.getvalue(), self.payload)

    def test_invite_includes_gui_open_music(self):
        song = music.CatalogItem(1, "a.mp3", "mp3", 12, "", Path("a.mp3"))
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

    def test_invite_shows_remote_origin(self):
        song = music.CatalogItem(2, "b.mp3", "mp3", 12, "node-b")
        text = server._music_invite_message(
            url="https://peer.example/music/tok",
            key="ABC123",
            song=song,
            lang="en",
        )
        self.assertIn("@node-b", text)

    def test_player_lists_federated_songs(self):
        prev_cat = music._player_catalog_hook
        prev_file = music._remote_file_hook
        music.set_player_catalog_hook(
            lambda: [
                music.CatalogItem(1, "track.mp3", "mp3", 10, "", Path("track.mp3")),
                music.CatalogItem(2, "peer.mp3", "mp3", 20, "node-b"),
            ]
        )
        music.set_remote_file_hook(
            lambda origin, name, nick: "https://peer.example/music/tok123"
        )
        music.music_store._origin_pages.clear()
        self.addCleanup(lambda: music.set_player_catalog_hook(prev_cat))
        self.addCleanup(lambda: music.set_remote_file_hook(prev_file))
        self.addCleanup(music.music_store._origin_pages.clear)

        sess = music.music_store.mint(2, "ada")
        handler = _FakeHandler(f"/music/{sess.token}")
        music_http.handle_music_get(handler)
        html = handler.body.getvalue().decode("utf-8")
        self.assertIn("peer.mp3", html)
        self.assertIn("node-b", html)
        self.assertIn("?origin=", html)

        ok = _FakeHandler(f"/music/{sess.token}/auth")
        ok.json_body = {"key": sess.key}
        music_http.handle_music_post(ok)
        songs = ok.json_out.get("songs") or []
        origins = {s["name"]: s.get("origin") for s in songs}
        self.assertEqual(origins.get("peer.mp3"), "node-b")
        self.assertEqual(origins.get("track.mp3"), "")

        remote = _FakeHandler(f"/music/{sess.token}/file/peer.mp3?origin=node-b")
        music_http.handle_music_get(remote)
        self.assertEqual(remote.status, 302)
        self.assertIn(
            ("Location", "https://peer.example/music/tok123/file/peer.mp3"),
            remote.sent_headers,
        )

    def test_origin_file_url_strips_query(self):
        self.assertEqual(
            music.origin_file_url(
                "https://host.example/music/abc?lang=zh", "a b.mp3"
            ),
            "https://host.example/music/abc/file/a%20b.mp3",
        )


class TestFederatedMusicCatalog(unittest.TestCase):
    def test_merge_union_sorts_and_indexes(self):
        local = [
            music.SongEntry(1, "zeta.mp3", "mp3", 10, Path("/z")),
            music.SongEntry(2, "alpha.mp3", "mp3", 20, Path("/a")),
        ]
        remote = {
            "node-b": [
                {"name": "alpha.mp3", "ext": "mp3", "size_bytes": 99},
                {"name": "beta.wav", "ext": "wav", "size_bytes": 5},
                {"name": "notes.txt", "ext": "txt", "size_bytes": 3},
            ],
            "node-c": [{"name": "gamma.ogg", "ext": "ogg", "size_bytes": 1000}],
        }
        catalog = music.merge_federated_catalog(local, remote)
        names = [(it.name, it.origin) for it in catalog]
        self.assertEqual(
            names,
            [
                ("alpha.mp3", ""),
                ("alpha.mp3", "node-b"),
                ("beta.wav", "node-b"),
                ("gamma.ogg", "node-c"),
                ("zeta.mp3", ""),
            ],
        )
        self.assertEqual([it.index for it in catalog], [1, 2, 3, 4, 5])
        self.assertTrue(catalog[1].is_remote)
        self.assertFalse(catalog[0].is_remote)

    def test_resolve_prefers_local_then_name_at_node(self):
        catalog = music.merge_federated_catalog(
            [music.SongEntry(1, "same.mp3", "mp3", 1, Path("/s"))],
            {"peer": [{"name": "same.mp3", "ext": "mp3", "size_bytes": 2}]},
        )
        local = music.resolve_catalog_item("same.mp3", catalog)
        assert local is not None
        self.assertEqual(local.origin, "")
        remote = music.resolve_catalog_item("same.mp3@peer", catalog)
        assert remote is not None
        self.assertEqual(remote.origin, "peer")
        by_idx = music.resolve_catalog_item("2", catalog)
        assert by_idx is not None
        self.assertEqual(by_idx.origin, "peer")

    def test_search_catalog_items_matches_origin(self):
        catalog = music.merge_federated_catalog(
            [],
            {"math": [{"name": "theme.mp3", "ext": "mp3", "size_bytes": 3}]},
        )
        hits = music.search_catalog_items(catalog, "math")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].name, "theme.mp3")


class MusicPlayFederationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "track.mp3").write_bytes(b"ID3" + b"\x00" * 20)
        self.prev = os.environ.get("SSHCHAT_MUSIC_DIR")
        os.environ["SSHCHAT_MUSIC_DIR"] = str(self.dir)
        self.addCleanup(self._restore)

    def _restore(self):
        self.tmp.cleanup()
        if self.prev is None:
            os.environ.pop("SSHCHAT_MUSIC_DIR", None)
        else:
            os.environ["SSHCHAT_MUSIC_DIR"] = self.prev

    def test_owner_play_request_mints_session(self):
        replies: list[tuple] = []

        class FakeHub:
            enabled = True
            node_id = "owner"

            def reply_music_play(self, requester, req_id, payload):
                replies.append((requester, req_id, payload))

        fake_http = mock.Mock()
        fake_http.get_base_url.return_value = "https://host.example"
        with mock.patch.object(server.federation, "get_hub", return_value=FakeHub()):
            with mock.patch.object(server, "file_http", fake_http):
                server._fed_on_music_play_request(
                    "owner", "rid", "track.mp3", "reader", "ada"
                )
        self.assertEqual(len(replies), 1)
        requester, req_id, payload = replies[0]
        self.assertEqual(requester, "reader")
        self.assertEqual(req_id, "rid")
        self.assertTrue(payload.get("ok"))
        self.assertEqual(payload.get("name"), "track.mp3")
        self.assertTrue(
            str(payload.get("url") or "").startswith("https://host.example/music/")
        )
        self.assertEqual(len(str(payload.get("key") or "")), 6)

    def test_owner_play_request_missing_song(self):
        replies: list[tuple] = []

        class FakeHub:
            enabled = True

            def reply_music_play(self, requester, req_id, payload):
                replies.append(payload)

        fake_http = mock.Mock()
        fake_http.get_base_url.return_value = "https://host.example"
        with mock.patch.object(server.federation, "get_hub", return_value=FakeHub()):
            with mock.patch.object(server, "file_http", fake_http):
                server._fed_on_music_play_request(
                    "owner", "rid", "nope.mp3", "reader", "ada"
                )
        self.assertFalse(replies[0].get("ok"))
        self.assertIn("not found", str(replies[0].get("error") or "").lower())


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
