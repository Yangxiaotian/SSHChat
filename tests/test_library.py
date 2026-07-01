"""Tests for library book listing and pagination."""
import os
import tempfile
import unittest
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
        self.assertTrue(self.store.clear_book("Carol", "x.epub"))
        self.assertIsNone(self.store.get_page("carol", "x.epub"))
        self.assertFalse(self.store.clear_book("carol", "x.epub"))

    def test_users_are_isolated(self) -> None:
        self.store.set_page("dave", "book.txt", 7)
        self.store.set_page("erin", "book.txt", 2)
        self.assertEqual(self.store.get_page("dave", "book.txt"), 7)
        self.assertEqual(self.store.get_page("erin", "book.txt"), 2)


class TestLibraryTxt(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.lib_dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_list_books_filters_extensions(self) -> None:
        (self.lib_dir / "a.txt").write_text("hello", encoding="utf-8")
        (self.lib_dir / "b.pdf").write_bytes(b"%PDF-1.4\n")
        (self.lib_dir / "skip.md").write_text("no", encoding="utf-8")
        books = library.list_books(self.lib_dir)
        self.assertEqual(len(books), 2)
        self.assertEqual(books[0].name, "a.txt")
        self.assertEqual(books[1].name, "b.pdf")

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


if __name__ == "__main__":
    unittest.main()
