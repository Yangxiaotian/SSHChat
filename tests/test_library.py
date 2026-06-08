"""Tests for library book listing and pagination."""
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

    def test_load_pdf_prefers_pymupdf_for_chinese(self) -> None:
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


if __name__ == "__main__":
    unittest.main()
