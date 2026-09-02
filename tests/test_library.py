"""Tests for library book listing and pagination."""
import json
import os
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

import library


class TestLibraryBookmarks(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store_path = str(Path(self.tmp.name) / "bookmarks.json")
        self.store = library.LibraryBookmarkStore(self.store_path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_set_and_get_page(self) -> None:
        self.store.set_page("Alice", "book.epub", 41)
        self.assertEqual(self.store.get_page("alice", "book.epub"), 41)
        self.assertEqual(self.store.get_page("Alice", "../book.epub"), 41)

    def test_list_for_user(self) -> None:
        self.store.set_page("bob", "a.txt", 3)
        self.store.set_page("bob", "b.pdf", 10)
        marks = self.store.list_for_user("Bob")
        self.assertEqual(marks, {"a.txt": 3, "b.pdf": 10})

    def test_clear_book(self) -> None:
        self.store.set_page("carol", "x.epub", 5)
        cleared = self.store.clear_book("Carol", "x.epub")
        self.assertIsNotNone(cleared)
        self.assertIsNone(self.store.get_page("carol", "x.epub"))
        self.assertIsNone(self.store.clear_book("carol", "x.epub"))

    def test_users_are_isolated(self) -> None:
        self.store.set_page("dave", "book.txt", 7)
        self.store.set_page("erin", "book.txt", 2)
        self.assertEqual(self.store.get_page("dave", "book.txt"), 7)
        self.assertEqual(self.store.get_page("erin", "book.txt"), 2)


class TestLibraryBookmarkFederationMerge(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = library.LibraryBookmarkStore(
            str(Path(self.tmp.name) / "bookmarks.json")
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_merge_lww_keeps_newer_page(self) -> None:
        self.store.set_page("yxt", "a.epub", 1)
        older = {"a.epub": {"page": 9, "updated_ts": 1}}
        newer = {"a.epub": {"page": 3, "updated_ts": int(time.time()) + 10}}
        self.assertTrue(self.store.merge_from_remote("YXT", newer))
        self.assertEqual(self.store.get_page("yxt", "a.epub"), 3)
        self.assertFalse(self.store.merge_from_remote("yxt", older))
        self.assertEqual(self.store.get_page("yxt", "a.epub"), 3)

    def test_merge_delete_tombstone(self) -> None:
        self.store.set_page("yxt", "peer::b.txt", 4)
        self.assertEqual(self.store.get_page("yxt", "b.txt"), 4)
        ts = int(time.time()) + 5
        self.assertTrue(
            self.store.merge_from_remote(
                "yxt", {"peer::b.txt": {"deleted": True, "updated_ts": ts}}
            )
        )
        self.assertIsNone(self.store.get_page("yxt", "b.txt"))
        self.assertIsNone(self.store.get_page("yxt", "peer::b.txt"))

    def test_export_includes_federated_keys(self) -> None:
        self.store.set_page("yxt", "Math::book.epub", 7)
        exported = self.store.export_user("yxt")
        self.assertIn("book.epub", exported)
        self.assertNotIn("Math::book.epub", exported)
        self.assertEqual(exported["book.epub"]["page"], 7)
        self.assertEqual(self.store.get_page("yxt", "Math::book.epub"), 7)

    def test_remote_origin_key_merges_into_bare_name(self) -> None:
        """Owner saved bare name; reader must resume via federated merge."""
        remote = {
            "中国近代史.epub": {"page": 41, "updated_ts": int(time.time())}
        }
        self.assertTrue(self.store.merge_from_remote("yxt", remote))
        self.assertEqual(self.store.get_page("yxt", "中国近代史.epub"), 41)
        self.assertEqual(
            self.store.get_page("yxt", "Mathematics.local::中国近代史.epub"), 41
        )


class TestLibraryTxt(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.lib_dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_list_books_filters_extensions(self) -> None:
        (self.lib_dir / "a.txt").write_text("hello", encoding="utf-8")
        (self.lib_dir / "b.pdf").write_bytes(b"%PDF-1.4\n")
        (self.lib_dir / "c.md").write_text("# title\n", encoding="utf-8")
        (self.lib_dir / "skip.docx").write_bytes(b"no")
        books = library.list_books(self.lib_dir)
        self.assertEqual(len(books), 3)
        self.assertEqual(books[0].name, "a.txt")
        self.assertEqual(books[1].name, "b.pdf")
        self.assertEqual(books[2].name, "c.md")

    def test_load_md_as_plain_text(self) -> None:
        path = self.lib_dir / "notes.md"
        path.write_text("# Hello\n\nworld " * 200, encoding="utf-8")
        old = library.LIBRARY_PAGE_CHARS
        library.LIBRARY_PAGE_CHARS = 500
        try:
            doc = library.load_book(path)
            self.assertEqual(doc.title, "notes")
            self.assertGreater(len(doc.pages), 1)
            self.assertTrue(doc.pages[0].startswith("# Hello"))
        finally:
            library.LIBRARY_PAGE_CHARS = old

    def test_load_txt_splits_pages(self) -> None:
        path = self.lib_dir / "long.txt"
        path.write_text("word " * 800, encoding="utf-8")
        old = library.LIBRARY_PAGE_CHARS
        library.LIBRARY_PAGE_CHARS = 500
        try:
            doc = library.load_book(path)
        finally:
            library.LIBRARY_PAGE_CHARS = old
        self.assertGreater(doc.total_pages, 1)
        self.assertEqual(doc.title, "long")

    def test_resolve_book_by_index_and_name(self) -> None:
        (self.lib_dir / "book.txt").write_text("x", encoding="utf-8")
        catalog = library.list_books(self.lib_dir)
        by_idx = library.resolve_book(self.lib_dir, "1", catalog)
        by_name = library.resolve_book(self.lib_dir, "book.txt", catalog)
        self.assertIsNotNone(by_idx)
        self.assertIsNotNone(by_name)
        self.assertEqual(by_idx.path, by_name.path)

    def test_resolve_rejects_path_traversal(self) -> None:
        catalog = library.list_books(self.lib_dir)
        self.assertIsNone(library.resolve_book(self.lib_dir, "../etc/passwd", catalog))
        self.assertIsNone(library.resolve_book(self.lib_dir, "foo/bar.txt", catalog))

    def test_search_catalog_by_name_and_ext(self) -> None:
        (self.lib_dir / "红楼梦.epub").write_text("x", encoding="utf-8")
        (self.lib_dir / "三国演义.txt").write_text("y", encoding="utf-8")
        (self.lib_dir / "notes.pdf").write_bytes(b"%PDF-1.4\n")
        catalog = library.list_books(self.lib_dir)
        self.assertEqual(len(library.search_catalog(catalog, "红楼")), 1)
        self.assertEqual(library.search_catalog(catalog, "红楼")[0].name, "红楼梦.epub")
        self.assertEqual(len(library.search_catalog(catalog, "pdf")), 1)
        self.assertEqual(len(library.search_catalog(catalog, "三国 txt")), 1)
        self.assertEqual(library.search_catalog(catalog, "missing"), [])

    def test_search_catalog_empty_query_returns_all(self) -> None:
        (self.lib_dir / "a.txt").write_text("x", encoding="utf-8")
        catalog = library.list_books(self.lib_dir)
        self.assertEqual(library.search_catalog(catalog, ""), catalog)
        self.assertEqual(library.search_catalog(catalog, "   "), catalog)


class TestLibrarySearchBook(unittest.TestCase):
    def test_search_book_returns_page_snippets(self) -> None:
        doc = library.BookDocument(
            title="demo",
            pages=["alpha foo bar", "nothing here", "foo again"],
            source_path=Path("demo.txt"),
        )
        hits = library.search_book(doc, "foo")
        self.assertEqual([h[0] for h in hits], [0, 2])
        self.assertIn("foo", hits[0][1])
        self.assertEqual(library.search_book(doc, "missing"), [])


class TestLibraryHtml(unittest.TestCase):
    def test_html_to_text(self) -> None:
        text = library._html_to_text("<p>Hello <b>world</b></p>")
        self.assertIn("Hello", text)
        self.assertIn("world", text)


class TestLibrarySplitPages(unittest.TestCase):
    def test_split_pages_preserves_all_characters(self) -> None:
        text = "中文测试" * 1200
        pages = library._split_pages(text, 500)
        self.assertEqual("".join(pages), text)

    def test_split_pages_keeps_ascii_word_count(self) -> None:
        text = "word " * 800
        pages = library._split_pages(text, 500)
        self.assertGreater(len(pages), 1)
        self.assertEqual("".join(pages).count("word"), text.count("word"))


class TestLibraryPdfExtract(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_pdf_text_quality_prefers_readable_chinese(self) -> None:
        good = "这是完整的中文正文。"
        bad = "Ùf/{,N\x00LN-eÿ"
        self.assertGreater(library._pdf_text_quality(good), library._pdf_text_quality(bad))

    def test_pick_best_pdf_text(self) -> None:
        chosen = library._pick_best_pdf_text(["Ù\x00garbage", "readable english", "中文正文"])
        self.assertEqual(chosen, "中文正文")

    def test_extract_pdf_page_text_pypdf_falls_back_to_plain(self) -> None:
        class _Page:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def extract_text(self, **kwargs: object) -> str:
                self.calls.append(kwargs)
                if kwargs:
                    raise TypeError("layout unsupported")
                return "plain text"

        page = _Page()
        self.assertEqual(library._extract_pdf_page_text_pypdf(page), "plain text")
        self.assertGreaterEqual(len(page.calls), 1)

    def test_pdf_text_quality_penalizes_spaced_cjk(self) -> None:
        continuous = "软件工程概述主要在选择题、论文题中都有考察。"
        spaced = "软 件 工 程 概 述 主 要 在 选 择 题 、 论 文 题 中 都 有 考 察 。"
        self.assertGreater(
            library._pdf_text_quality(continuous),
            library._pdf_text_quality(spaced),
        )

    def test_pick_best_pdf_text_prefers_continuous_cjk(self) -> None:
        continuous = "1.软件工程概述（重点）\n1.1.软件工程相关定义（次重点）\n1.2.软件开发方法（重点）"
        spaced = "1 . 2 . 软 件 开 发 方 法\n1 . 1 . 软 件 工 程 相 关 定 义\n1 . 软 件 工 程 概 述"
        chosen = library._pick_best_pdf_text([spaced, continuous])
        self.assertEqual(chosen, continuous)

    def test_load_pdf_skips_pypdf_when_pymupdf_succeeds(self) -> None:
        try:
            import fitz
        except ImportError:
            self.skipTest("pymupdf not installed")
        path = Path(self.tmp.name) / "fast.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "中文正文", fontsize=12, fontname="china-s")
        doc.save(path)
        doc.close()

        calls: list[str] = []
        orig_pypdf = library._load_pdf_pypdf
        orig_pymupdf = library._load_pdf_pymupdf

        def _track_pypdf(p: Path) -> library.BookDocument:
            calls.append("pypdf")
            return orig_pypdf(p)

        def _track_pymupdf(p: Path) -> library.BookDocument:
            calls.append("pymupdf")
            return orig_pymupdf(p)

        old_compare = os.environ.get("SSHCHAT_PDF_COMPARE_ENGINES")
        os.environ["SSHCHAT_PDF_COMPARE_ENGINES"] = "0"
        try:
            library._load_pdf_pypdf = _track_pypdf  # type: ignore[assignment]
            library._load_pdf_pymupdf = _track_pymupdf  # type: ignore[assignment]
            loaded = library._load_pdf(path)
        finally:
            library._load_pdf_pypdf = orig_pypdf  # type: ignore[assignment]
            library._load_pdf_pymupdf = orig_pymupdf  # type: ignore[assignment]
            if old_compare is None:
                os.environ.pop("SSHCHAT_PDF_COMPARE_ENGINES", None)
            else:
                os.environ["SSHCHAT_PDF_COMPARE_ENGINES"] = old_compare

        self.assertIn("中文正文", "\n".join(loaded.pages))
        self.assertEqual(calls, ["pymupdf"])

    def test_load_pdf_prefers_pymupdf_reading_order(self) -> None:
        try:
            import fitz
        except ImportError:
            self.skipTest("pymupdf not installed")
        path = Path(self.tmp.name) / "cjk.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "第二行中间有重要内容不应丢失。", fontsize=12, fontname="china-s")
        doc.save(path)
        doc.close()
        loaded = library.load_book(path)
        full = "\n".join(loaded.pages)
        self.assertIn("中间有重要内容", full)

    def test_layout_fragments_reads_top_to_bottom(self) -> None:
        fragments = [
            (429.0, 72.0, "府邸做直播，"),
            (409.0, 72.0, "错。还有一位记者，在香港特首"),
            (72.5, 72.0, "万幸，直播时没出"),
        ]
        text = library._layout_fragments_to_text(fragments).replace("\n", "")
        self.assertLess(text.find("万幸，直播时没出"), text.find("错。还有一位记者"))
        self.assertLess(text.find("错。还有一位记者"), text.find("府邸做直播"))

    def test_pick_best_pdf_text_prefers_longer_on_tie(self) -> None:
        short = "万幸，直播时没出府邸做直播"
        long = "万幸，直播时没出错。还有一位记者，在香港特首府邸做直播"
        chosen = library._pick_best_pdf_text([short, long])
        self.assertEqual(chosen, long)

    def test_pick_best_pdf_text_drops_strict_substring(self) -> None:
        partial = "万幸，直播时没出"
        full = "万幸，直播时没出错。还有一位记者，在香港特首府邸做直播"
        chosen = library._pick_best_pdf_text([partial, full])
        self.assertEqual(chosen, full)

    def test_load_pdf_preserves_paragraph_boundary_phrase(self) -> None:
        try:
            import fitz
        except ImportError:
            self.skipTest("pymupdf not installed")

        phrase = "直播时没出错。还有一位记者，在香港特首"
        body = (
            "香港回归时，中央电视台第一次对大型新闻进行现场直播，这是每一个新闻人热切期盼的事情。"
            "我进台工作后问过当时的直播组，他们都说印象非常深。有一个梗是，在彩排驻港部队零点跨越罗湖口岸进入香港这个场景时，"
            "我们有一位主持人两次激动得口误，把驻港部队说成戒严部队，领导就叮嘱说，你可要好好练，"
            "如果直播时来这么一个口误，那不光你完蛋了，我们整个团队都完蛋了。万幸，直播时没出错。"
            "还有一位记者，在香港特首府邸做直播，按照既定程序，英国派驻的最后一位特首彭定康会定时定点离开特首府。"
        )
        split_at = body.index("错。还有一位记者")
        part_a = body[:split_at]
        part_b = "错。还有一位记者，在香港特首"
        part_c = body[body.index("府邸") :]

        path = Path(self.tmp.name) / "paragraph-gap.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_textbox(fitz.Rect(72, 72, 523, 400), part_a, fontsize=11, fontname="china-s")
        page.insert_text((72, 420), part_b, fontsize=11, fontname="china-s")
        page.insert_text((72, 440), part_c, fontsize=11, fontname="china-s")
        doc.save(path)
        doc.close()

        loaded = library.load_book(path)
        full = "\n".join(loaded.pages).replace("\n", "")
        self.assertIn(phrase, full)
        idx = full.find("直播时没出")
        self.assertGreaterEqual(idx, 0)
        self.assertEqual(full[idx : idx + len(phrase)], phrase)


class TestFederatedCatalog(unittest.TestCase):
    def test_merge_union_sorts_and_indexes(self) -> None:
        local = [
            library.BookEntry(1, "zeta.txt", "txt", 10, Path("/z")),
            library.BookEntry(2, "alpha.txt", "txt", 20, Path("/a")),
        ]
        remote = {
            "node-b": [
                {"name": "alpha.txt", "ext": "txt", "size_bytes": 99},
                {"name": "beta.md", "ext": "md", "size_bytes": 5},
            ],
            "node-c": [{"name": "gamma.pdf", "ext": "pdf", "size_bytes": 1000}],
        }
        catalog = library.merge_federated_catalog(local, remote)
        names = [(it.name, it.origin) for it in catalog]
        self.assertEqual(
            names,
            [
                ("alpha.txt", ""),
                ("alpha.txt", "node-b"),
                ("beta.md", "node-b"),
                ("gamma.pdf", "node-c"),
                ("zeta.txt", ""),
            ],
        )
        self.assertEqual([it.index for it in catalog], [1, 2, 3, 4, 5])
        self.assertTrue(catalog[1].is_remote)
        self.assertFalse(catalog[0].is_remote)

    def test_resolve_prefers_local_then_name_at_node(self) -> None:
        catalog = library.merge_federated_catalog(
            [library.BookEntry(1, "same.txt", "txt", 1, Path("/s"))],
            {"peer": [{"name": "same.txt", "ext": "txt", "size_bytes": 2}]},
        )
        local = library.resolve_catalog_item("same.txt", catalog)
        assert local is not None
        self.assertEqual(local.origin, "")
        remote = library.resolve_catalog_item("same.txt@peer", catalog)
        assert remote is not None
        self.assertEqual(remote.origin, "peer")
        by_idx = library.resolve_catalog_item("2", catalog)
        assert by_idx is not None
        self.assertEqual(by_idx.origin, "peer")

    def test_search_catalog_items_matches_origin(self) -> None:
        catalog = library.merge_federated_catalog(
            [],
            {"math": [{"name": "calc.txt", "ext": "txt", "size_bytes": 3}]},
        )
        hits = library.search_catalog_items(catalog, "math")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].name, "calc.txt")


class TestLibraryIsolatedLoad(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.lib_dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_txt_stays_in_process(self) -> None:
        path = self.lib_dir / "note.txt"
        path.write_text("hello isolated", encoding="utf-8")
        doc = library.load_book_isolated(path)
        self.assertEqual(doc.title, "note")
        self.assertIn("hello isolated", doc.pages[0])

    def test_epub_uses_subprocess(self) -> None:
        path = self.lib_dir / "demo.epub"
        path.write_bytes(b"PK\x03\x04fake")
        calls: list[list[str]] = []

        class FakeProc:
            returncode = 0
            stderr = b""

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            out = Path(cmd[-1])
            out.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "title": "demo",
                        "pages": ["page one", "page two"],
                        "source_path": str(path),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return FakeProc()

        with unittest.mock.patch("subprocess.run", side_effect=fake_run):
            doc = library.load_book_isolated(path, timeout=30)
        self.assertEqual(doc.title, "demo")
        self.assertEqual(doc.pages, ["page one", "page two"])
        self.assertTrue(calls)
        self.assertIn("-c", calls[0])

    def test_subprocess_error_surfaces(self) -> None:
        path = self.lib_dir / "bad.epub"
        path.write_bytes(b"nope")

        class FakeProc:
            returncode = 1
            stderr = b"boom"

        def fake_run(cmd, **kwargs):
            out = Path(cmd[-1])
            out.write_text(
                json.dumps({"ok": False, "error": "RuntimeError: boom"}),
                encoding="utf-8",
            )
            return FakeProc()

        with unittest.mock.patch("subprocess.run", side_effect=fake_run):
            with self.assertRaises(RuntimeError) as ctx:
                library.load_book_isolated(path)
        self.assertIn("boom", str(ctx.exception))


class TestWrapOutputLines(unittest.TestCase):
    def test_splits_before_utf8_byte_budget(self) -> None:
        # 30 CJK chars = 90 UTF-8 bytes; must split under default 78-byte budget.
        line = "[*] " + ("测" * 30) + "\n"
        parts = library.wrap_output_lines(line)
        self.assertGreater(len(parts), 1)
        content = "".join(
            p.rstrip("\n")[4:] if p.rstrip("\n").startswith("[*] ") else p.rstrip("\n")
            for p in parts
        )
        self.assertEqual(content, "测" * 30)
        for part in parts:
            p = part.rstrip("\n")
            self.assertTrue(p.startswith("[*] "))
            self.assertLessEqual(
                len(p.encode("utf-8")),
                library.LIBRARY_WRAP_BYTES,
            )

    def test_short_line_unchanged(self) -> None:
        line = "[*] short\n"
        self.assertEqual(library.wrap_output_lines(line), [line])


if __name__ == "__main__":
    unittest.main()
