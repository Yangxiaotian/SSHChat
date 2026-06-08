"""Server-side library: list and paginate books (txt, pdf, epub) under a directory."""
from __future__ import annotations

import json
import os
import re
import tempfile
import textwrap
import threading
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Optional

LIBRARY_EXTENSIONS = {".txt", ".pdf", ".epub"}
LIBRARY_PAGE_CHARS = int(os.environ.get("SSHCHAT_LIBRARY_PAGE_CHARS", "2500"))
LIBRARY_WRAP_WIDTH = int(os.environ.get("SSHCHAT_LIBRARY_WRAP", "88"))
LIBRARY_LIST_PREVIEW_CHARS = int(os.environ.get("SSHCHAT_LIBRARY_PREVIEW_CHARS", "400"))


class _HtmlTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self._parts.append(text)

    def get_text(self) -> str:
        return "\n".join(self._parts)


def _html_to_text(html: str) -> str:
    parser = _HtmlTextExtractor()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        return re.sub(r"<[^>]+>", " ", html or "")
    return parser.get_text()


def _split_pages(text: str, page_chars: int) -> list[str]:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return [""]
    pages: list[str] = []
    start = 0
    length = len(text)
    while start < length:
        end = min(start + page_chars, length)
        if end < length:
            break_at = text.rfind("\n\n", start, end)
            if break_at > start:
                end = break_at + 2 if text[break_at : break_at + 2] == "\n\n" else break_at + 1
            else:
                break_at = text.rfind("\n", start, end)
                if break_at > start:
                    end = break_at + 1
                else:
                    break_at = text.rfind(" ", start, end)
                    if break_at > start:
                        end = break_at + 1
        chunk = text[start:end].strip()
        if chunk:
            pages.append(chunk)
        if end <= start:
            end = min(start + page_chars, length)
            chunk = text[start:end].strip()
            if chunk:
                pages.append(chunk)
            start = end
        else:
            start = end
    return pages or [""]


@dataclass(frozen=True)
class BookEntry:
    index: int
    name: str
    ext: str
    size_bytes: int
    path: Path


@dataclass
class BookDocument:
    title: str
    pages: list[str]
    source_path: Path

    @property
    def total_pages(self) -> int:
        return max(1, len(self.pages))


def default_library_dir() -> Path:
    raw = os.environ.get("SSHCHAT_LIBRARY_DIR", "").strip()
    if raw:
        return Path(raw)
    return Path("/opt/sshchat/library")


def list_books(library_dir: Path) -> list[BookEntry]:
    if not library_dir.is_dir():
        return []
    entries: list[BookEntry] = []
    for path in sorted(library_dir.iterdir(), key=lambda p: p.name.lower()):
        if not path.is_file():
            continue
        ext = path.suffix.lower()
        if ext not in LIBRARY_EXTENSIONS:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        entries.append(
            BookEntry(
                index=len(entries) + 1,
                name=path.name,
                ext=ext.lstrip("."),
                size_bytes=size,
                path=path,
            )
        )
    return entries


def resolve_book(library_dir: Path, token: str, catalog: list[BookEntry]) -> Optional[BookEntry]:
    token = (token or "").strip()
    if not token:
        return None
    if token.isdigit():
        idx = int(token)
        for entry in catalog:
            if entry.index == idx:
                return entry
        return None
    safe_name = Path(token).name
    if safe_name != token or ".." in token or "/" in token or "\\" in token:
        return None
    for entry in catalog:
        if entry.name == safe_name:
            return entry
    return None


def _load_txt(path: Path) -> BookDocument:
    data = path.read_text(encoding="utf-8", errors="replace")
    pages = _split_pages(data, LIBRARY_PAGE_CHARS)
    return BookDocument(title=path.stem, pages=pages, source_path=path)


def _pdf_text_quality(text: str) -> tuple[int, int, int]:
    """Score extracted PDF text; higher is better."""
    if not text:
        return (0, 0, 0)
    readable = 0
    cjk = 0
    penalty = 0
    for ch in text:
        code = ord(ch)
        if ch in "\n\r\t":
            continue
        if 0x4E00 <= code <= 0x9FFF:
            cjk += 1
            readable += 2
        elif ch.isalnum():
            readable += 1
        elif ch in "，。！？；：、（）《》—…·“”‘’":
            readable += 1
        elif ch == "\ufffd":
            penalty += 8
        elif code < 32 or code == 0xFFFD:
            penalty += 6
        elif code < 0x80 and not ch.isprintable():
            penalty += 4
    return (cjk, readable - penalty, len(text.strip()))


def _pick_best_pdf_text(candidates: list[str]) -> str:
    best = ""
    best_score = (-1, -1, -1)
    for text in candidates:
        score = _pdf_text_quality(text)
        if score > best_score:
            best = text
            best_score = score
    return best


def _layout_fragments_to_text(fragments: list[tuple[float, float, str]]) -> str:
    if not fragments:
        return ""
    ordered = sorted(fragments, key=lambda item: (-round(item[0], 1), item[1]))
    parts: list[str] = []
    prev_y: Optional[float] = None
    for y, _x, text in ordered:
        if not text:
            continue
        if prev_y is not None and abs(y - prev_y) > 4:
            if parts and not parts[-1].endswith("\n"):
                parts.append("\n")
        elif parts and not parts[-1].endswith(("\n", " ")):
            parts.append(" ")
        parts.append(text)
        prev_y = y
    return "".join(parts)


def _extract_pdf_page_text_pypdf_visitor(page: object) -> str:
    fragments: list[tuple[float, float, str]] = []

    def visitor_text(
        text: str,
        _cm: object,
        tm: object,
        _font_dict: object,
        _font_size: object,
    ) -> None:
        if not text:
            return
        try:
            tm_list = list(tm)  # type: ignore[arg-type]
            x = float(tm_list[4])
            y = float(tm_list[5])
        except Exception:
            x, y = 0.0, 0.0
        fragments.append((y, x, text))

    try:
        page.extract_text(visitor_text=visitor_text)  # type: ignore[union-attr]
    except Exception:
        return ""
    return _layout_fragments_to_text(fragments)


def _extract_pdf_page_text_pypdf(page: object) -> str:
    candidates: list[str] = []
    attempts: list[dict[str, object]] = [
        {},
        {"extraction_mode": "layout", "layout_mode_space_vertically": False},
    ]
    for kwargs in attempts:
        try:
            text = page.extract_text(**kwargs) or ""  # type: ignore[union-attr]
        except TypeError:
            try:
                text = page.extract_text() or ""  # type: ignore[union-attr]
            except Exception:
                text = ""
        except Exception:
            text = ""
        if text.strip():
            candidates.append(text)
    visitor_text = _extract_pdf_page_text_pypdf_visitor(page)
    if visitor_text.strip():
        candidates.append(visitor_text)
    return _pick_best_pdf_text(candidates)


def _load_pdf_pymupdf(path: Path) -> Optional[BookDocument]:
    try:
        import fitz
    except ImportError:
        return None
    pages: list[str] = []
    try:
        with fitz.open(str(path)) as doc:
            for page in doc:
                text = page.get_text("text") or ""
                text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
                if not text:
                    pages.append("（本页未能提取文字，可能是扫描版 PDF）")
                else:
                    pages.extend(_split_pages(text, LIBRARY_PAGE_CHARS))
    except Exception:
        return None
    if not pages:
        pages = ["（未能从 PDF 提取文字）"]
    return BookDocument(title=path.stem, pages=pages, source_path=path)


def _load_pdf_pypdf(path: Path) -> BookDocument:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("PDF 阅读需要安装 pypdf（服务端 pip install pypdf）") from exc
    reader = PdfReader(str(path))
    pages: list[str] = []
    for page in reader.pages:
        text = _extract_pdf_page_text_pypdf(page)
        text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not text:
            pages.append("（本页未能提取文字，可能是扫描版 PDF）")
        else:
            pages.extend(_split_pages(text, LIBRARY_PAGE_CHARS))
    if not pages:
        pages = ["（未能从 PDF 提取文字）"]
    return BookDocument(title=path.stem, pages=pages, source_path=path)


def _load_pdf(path: Path) -> BookDocument:
    doc = _load_pdf_pymupdf(path)
    if doc is not None:
        return doc
    return _load_pdf_pypdf(path)


def _load_epub(path: Path) -> BookDocument:
    try:
        import ebooklib
        from ebooklib import epub
    except ImportError as exc:
        raise RuntimeError("EPUB 阅读需要安装 ebooklib（服务端 pip install ebooklib）") from exc

    book = epub.read_epub(str(path))
    chunks: list[str] = []
    for item in book.get_items():
        if item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue
        try:
            content = item.get_content().decode("utf-8", errors="replace")
        except Exception:
            continue
        text = _html_to_text(content).strip()
        if text:
            chunks.append(text)
    full_text = "\n\n".join(chunks)
    pages = _split_pages(full_text, LIBRARY_PAGE_CHARS)
    title = path.stem
    try:
        meta_title = book.get_metadata("DC", "title")
        if meta_title and meta_title[0][0]:
            title = str(meta_title[0][0]).strip() or title
    except Exception:
        pass
    return BookDocument(title=title, pages=pages, source_path=path)


def load_book(path: Path) -> BookDocument:
    ext = path.suffix.lower()
    if ext == ".txt":
        return _load_txt(path)
    if ext == ".pdf":
        return _load_pdf(path)
    if ext == ".epub":
        return _load_epub(path)
    raise ValueError(f"不支持的格式：{ext}")


def format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def search_book(
    doc: BookDocument,
    query: str,
    max_results: int = 20,
    snippet_context: int = 50,
) -> list[tuple[int, str]]:
    """Search all pages for *query* (case-insensitive).

    Returns a list of ``(page_index, snippet)`` tuples in page order.
    The snippet is a short excerpt of the surrounding text with the match
    approximately centred, suitable for display in a single terminal line.
    """
    if not query:
        return []
    query_lower = query.lower()
    results: list[tuple[int, str]] = []
    for page_idx, page_text in enumerate(doc.pages):
        page_lower = page_text.lower()
        pos = page_lower.find(query_lower)
        if pos == -1:
            continue
        start = max(0, pos - snippet_context)
        end = min(len(page_text), pos + len(query) + snippet_context)
        snippet = page_text[start:end].replace("\n", " ").replace("\r", " ").strip()
        if start > 0:
            snippet = "…" + snippet
        if end < len(page_text):
            snippet = snippet + "…"
        results.append((page_idx, snippet))
        if len(results) >= max_results:
            break
    return results


def wrap_page_lines(text: str, width: int = LIBRARY_WRAP_WIDTH) -> list[str]:
    text = (text or "").strip()
    if not text:
        return ["（空白页）"]
    return textwrap.wrap(
        text,
        width=max(40, width),
        break_long_words=True,
        break_on_hyphens=False,
    )


def _normalize_user(name: str) -> str:
    return (name or "").strip().lower()


class LibraryBookmarkStore:
    """Per-user reading bookmarks persisted as JSON on disk."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._cache: dict[str, Any] | None = None

    def _empty_data(self) -> dict[str, Any]:
        return {"version": 1, "users": {}}

    def _ensure_loaded_locked(self) -> None:
        if self._cache is not None:
            return
        if not os.path.exists(self.path):
            self._cache = self._empty_data()
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = self._empty_data()
        users = data.get("users")
        if not isinstance(users, dict):
            data = self._empty_data()
        self._cache = data

    def _save_locked(self) -> None:
        assert self._cache is not None
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            prefix=".library-bookmarks-",
            suffix=".json",
            dir=directory,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, ensure_ascii=False, indent=2, sort_keys=True)
                f.write("\n")
            os.replace(tmp_path, self.path)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def get_page(self, user: str, book_name: str) -> Optional[int]:
        key = _normalize_user(user)
        book_key = Path(book_name).name
        if not key or not book_key:
            return None
        with self._lock:
            self._ensure_loaded_locked()
            assert self._cache is not None
            users = self._cache["users"]
            entry = users.get(key, {}).get(book_key)
            if not isinstance(entry, dict):
                return None
            try:
                page = int(entry.get("page", 0))
            except (TypeError, ValueError):
                return None
            return max(0, page)

    def set_page(self, user: str, book_name: str, page: int) -> None:
        key = _normalize_user(user)
        book_key = Path(book_name).name
        if not key or not book_key:
            return
        page = max(0, int(page))
        with self._lock:
            self._ensure_loaded_locked()
            assert self._cache is not None
            users = self._cache["users"]
            user_books = users.setdefault(key, {})
            if not isinstance(user_books, dict):
                user_books = {}
                users[key] = user_books
            user_books[book_key] = {
                "page": page,
                "updated_ts": int(time.time()),
            }
            self._save_locked()

    def list_for_user(self, user: str) -> dict[str, int]:
        key = _normalize_user(user)
        with self._lock:
            self._ensure_loaded_locked()
            assert self._cache is not None
            raw = self._cache["users"].get(key, {})
            if not isinstance(raw, dict):
                return {}
            out: dict[str, int] = {}
            for book_name, entry in raw.items():
                if isinstance(entry, dict):
                    try:
                        out[str(book_name)] = max(0, int(entry.get("page", 0)))
                    except (TypeError, ValueError):
                        continue
            return out

    def clear_book(self, user: str, book_name: str) -> bool:
        key = _normalize_user(user)
        book_key = Path(book_name).name
        if not key or not book_key:
            return False
        with self._lock:
            self._ensure_loaded_locked()
            assert self._cache is not None
            user_books = self._cache["users"].get(key)
            if not isinstance(user_books, dict) or book_key not in user_books:
                return False
            del user_books[book_key]
            if not user_books:
                self._cache["users"].pop(key, None)
            self._save_locked()
            return True
