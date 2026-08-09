"""Server-side library: list and paginate books (txt, md, pdf, epub) under a directory."""
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

LIBRARY_EXTENSIONS = {".txt", ".md", ".pdf", ".epub"}
LIBRARY_PAGE_CHARS = int(os.environ.get("SSHCHAT_LIBRARY_PAGE_CHARS", "2500"))
LIBRARY_WRAP_WIDTH = int(os.environ.get("SSHCHAT_LIBRARY_WRAP", "88"))
# Many mobile SSH terminals wrap by UTF-8 bytes (~80) not Unicode columns; keep
# each emitted line under this byte budget so CJK chars are not split mid-codepoint.
LIBRARY_WRAP_BYTES = int(os.environ.get("SSHCHAT_LIBRARY_WRAP_BYTES", "78"))
LIBRARY_LIST_PREVIEW_CHARS = int(os.environ.get("SSHCHAT_LIBRARY_PREVIEW_CHARS", "400"))

_BLOCK_HTML_TAGS = frozenset({
    "p",
    "div",
    "section",
    "article",
    "header",
    "footer",
    "aside",
    "li",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "tr",
    "table",
    "blockquote",
    "pre",
    "figure",
    "figcaption",
    "dd",
    "dt",
})
_SKIP_HTML_TAGS = frozenset({"script", "style", "head", "meta", "link", "svg"})


class _HtmlTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: object) -> None:
        lowered = tag.lower()
        if lowered in _SKIP_HTML_TAGS:
            self._skip_depth += 1
        elif lowered in _BLOCK_HTML_TAGS:
            self._append_break()
        elif lowered == "br":
            self._append_break()

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in _SKIP_HTML_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif lowered in _BLOCK_HTML_TAGS:
            self._append_break()

    def _append_break(self) -> None:
        if self._parts and not self._parts[-1].endswith("\n"):
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not data:
            return
        if data.isspace():
            if self._parts and not self._parts[-1].endswith((" ", "\n")):
                self._parts.append(" ")
            return
        self._parts.append(data)

    def get_text(self) -> str:
        text = "".join(self._parts)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n[ \t]+", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r" {2,}", " ", text)
        return text.strip()


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


@dataclass(frozen=True)
class CatalogItem:
    """Unified library row for local + federated catalogs."""

    index: int
    name: str
    ext: str
    size_bytes: int
    origin: str  # "" = local; otherwise owning peer node_id
    path: Optional[Path] = None

    @property
    def is_remote(self) -> bool:
        return bool(self.origin)

    def display_origin(self) -> str:
        return f" @{self.origin}" if self.origin else ""


def book_entry_to_meta(entry: BookEntry) -> dict[str, Any]:
    return {
        "name": entry.name,
        "ext": entry.ext,
        "size_bytes": int(entry.size_bytes),
    }


def merge_federated_catalog(
    local: list[BookEntry],
    remote_by_node: dict[str, list[dict[str, Any]]],
) -> list[CatalogItem]:
    """Union local books with remote catalogs; stable sort by name then origin."""
    items: list[CatalogItem] = []
    for entry in local:
        items.append(
            CatalogItem(
                index=0,
                name=entry.name,
                ext=entry.ext,
                size_bytes=entry.size_bytes,
                origin="",
                path=entry.path,
            )
        )
    for node_id, rows in (remote_by_node or {}).items():
        node_id = str(node_id or "").strip()
        if not node_id:
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            if not name or Path(name).name != name:
                continue
            ext = str(row.get("ext") or Path(name).suffix.lstrip(".")).lower()
            try:
                size = int(row.get("size_bytes") or 0)
            except (TypeError, ValueError):
                size = 0
            items.append(
                CatalogItem(
                    index=0,
                    name=name,
                    ext=ext,
                    size_bytes=size,
                    origin=node_id,
                    path=None,
                )
            )
    items.sort(key=lambda it: (it.name.lower(), it.origin.lower()))
    return [
        CatalogItem(
            index=i + 1,
            name=it.name,
            ext=it.ext,
            size_bytes=it.size_bytes,
            origin=it.origin,
            path=it.path,
        )
        for i, it in enumerate(items)
    ]


def search_catalog_items(catalog: list[CatalogItem], query: str) -> list[CatalogItem]:
    query = (query or "").strip().lower()
    if not query:
        return list(catalog)
    terms = query.split()
    results: list[CatalogItem] = []
    for entry in catalog:
        haystack = f"{entry.name} {entry.ext} {entry.origin}".lower()
        if all(term in haystack for term in terms):
            results.append(entry)
    return results


def resolve_catalog_item(
    token: str, catalog: list[CatalogItem]
) -> Optional[CatalogItem]:
    token = (token or "").strip()
    if not token:
        return None
    if token.isdigit():
        idx = int(token)
        for entry in catalog:
            if entry.index == idx:
                return entry
        return None
    # Optional "name@node" form for disambiguation.
    origin = ""
    name = token
    if "@" in token and not token.startswith("@"):
        name, origin = token.rsplit("@", 1)
        name = name.strip()
        origin = origin.strip()
    safe_name = Path(name).name
    if safe_name != name or ".." in name or "/" in name or "\\" in name:
        return None
    matches = [
        entry
        for entry in catalog
        if entry.name == safe_name and (not origin or entry.origin == origin)
    ]
    if len(matches) == 1:
        return matches[0]
    if not origin:
        local = [entry for entry in matches if not entry.origin]
        if len(local) == 1:
            return local[0]
    return None


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


def search_catalog(catalog: list[BookEntry], query: str) -> list[BookEntry]:
    """Filter *catalog* by filename/extension keywords (case-insensitive, all terms must match)."""
    query = (query or "").strip().lower()
    if not query:
        return list(catalog)
    terms = query.split()
    results: list[BookEntry] = []
    for entry in catalog:
        haystack = f"{entry.name} {entry.ext}".lower()
        if all(term in haystack for term in terms):
            results.append(entry)
    return results


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


def _pdf_cjk_spacing_penalty(text: str) -> int:
    """Penalize layout-mode artifacts that insert spaces between CJK glyphs."""
    if not text:
        return 0
    return len(re.findall(r"[\u4e00-\u9fff]\s+[\u4e00-\u9fff]", text))


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
    penalty += _pdf_cjk_spacing_penalty(text) * 4
    return (cjk, readable - penalty, len(text.strip()))


def _pick_best_pdf_text(candidates: list[str]) -> str:
    nonempty = [text for text in candidates if (text or "").strip()]
    if not nonempty:
        return ""
    # Drop strict substrings so a shorter partial extraction cannot beat a fuller one.
    kept: list[str] = []
    for text in nonempty:
        if any(text in other and text != other for other in nonempty):
            continue
        kept.append(text)
    if not kept:
        kept = nonempty
    best = ""
    best_score = (-1, -1, -1)
    for text in kept:
        score = _pdf_text_quality(text)
        if score > best_score:
            best = text
            best_score = score
        elif score == best_score and len(text.strip()) > len(best.strip()):
            best = text
    return best


def _layout_fragments_to_text(fragments: list[tuple[float, float, str]]) -> str:
    if not fragments:
        return ""
    # PDF user-space y grows downward; read top-to-bottom, then left-to-right.
    ordered = sorted(fragments, key=lambda item: (round(item[0], 1), item[1]))
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


def _extract_pdf_page_text_pymupdf_blocks(page: object) -> str:
    try:
        raw_blocks = page.get_text("blocks") or []  # type: ignore[union-attr]
    except Exception:
        return ""
    fragments: list[tuple[float, float, str]] = []
    for block in raw_blocks:
        if len(block) < 7 or block[6] != 0:
            continue
        text = (block[4] or "").strip()
        if not text:
            continue
        fragments.append((float(block[1]), float(block[0]), text))
    return _layout_fragments_to_text(fragments)


def _extract_pdf_page_text_pymupdf_dict(page: object) -> str:
    try:
        data = page.get_text("dict") or {}  # type: ignore[union-attr]
    except Exception:
        return ""
    fragments: list[tuple[float, float, str]] = []
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = (span.get("text") or "").strip()
                if not text:
                    continue
                bbox = span.get("bbox") or (0, 0, 0, 0)
                fragments.append((float(bbox[1]), float(bbox[0]), text))
    return _layout_fragments_to_text(fragments)


def _extract_pdf_page_text_pymupdf(page: object) -> str:
    candidates: list[str] = []
    unsorted = ""
    try:
        unsorted = page.get_text("text") or ""  # type: ignore[union-attr]
    except Exception:
        unsorted = ""
    if unsorted.strip():
        candidates.append(unsorted)
    try:
        sorted_text = page.get_text("text", sort=True) or ""  # type: ignore[union-attr]
    except TypeError:
        sorted_text = ""
    except Exception:
        sorted_text = ""
    if sorted_text.strip():
        # sort=True can scramble multi-column pages; only keep when clearly better.
        if (
            not unsorted.strip()
            or _pdf_text_quality(sorted_text) > _pdf_text_quality(unsorted)
        ):
            candidates.append(sorted_text)
    for extractor in (
        _extract_pdf_page_text_pymupdf_blocks,
        _extract_pdf_page_text_pymupdf_dict,
    ):
        text = extractor(page)
        if text.strip():
            candidates.append(text)
    return _pick_best_pdf_text(candidates)


def _pdf_document_quality(pages: list[str]) -> tuple[int, int, int]:
    return _pdf_text_quality("\n".join(pages))


def _load_pdf_pymupdf(path: Path) -> Optional[BookDocument]:
    try:
        import fitz
    except ImportError:
        return None
    pages: list[str] = []
    try:
        with fitz.open(str(path)) as doc:
            for page in doc:
                text = _extract_pdf_page_text_pymupdf(page)
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
    compare_engines = os.environ.get("SSHCHAT_PDF_COMPARE_ENGINES", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    pymupdf_doc: Optional[BookDocument] = None
    try:
        pymupdf_doc = _load_pdf_pymupdf(path)
    except Exception:
        pymupdf_doc = None
    if pymupdf_doc is not None and not compare_engines:
        if _pdf_document_quality(pymupdf_doc.pages)[1] > 0:
            return pymupdf_doc
    pypdf_doc: Optional[BookDocument] = None
    try:
        pypdf_doc = _load_pdf_pypdf(path)
    except Exception:
        pypdf_doc = None
    if pymupdf_doc is None:
        if pypdf_doc is None:
            raise RuntimeError("无法读取 PDF（pymupdf 与 pypdf 均失败）")
        return pypdf_doc
    if pypdf_doc is None:
        return pymupdf_doc
    pymupdf_score = _pdf_document_quality(pymupdf_doc.pages)
    pypdf_score = _pdf_document_quality(pypdf_doc.pages)
    # PyMuPDF preserves top-to-bottom reading order better for CJK textbooks.
    if pypdf_score > pymupdf_score:
        pym_readable = pymupdf_score[1]
        pyp_readable = pypdf_score[1]
        if pym_readable > 0 and pyp_readable <= int(pym_readable * 1.12):
            return pymupdf_doc
        return pypdf_doc
    if pypdf_score == pymupdf_score and sum(len(p) for p in pypdf_doc.pages) > sum(
        len(p) for p in pymupdf_doc.pages
    ):
        pym_readable = pymupdf_score[1]
        pyp_readable = pypdf_score[1]
        if pym_readable > 0 and pyp_readable <= int(pym_readable * 1.12):
            return pymupdf_doc
        return pypdf_doc
    return pymupdf_doc


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
    if ext in {".txt", ".md"}:
        return _load_txt(path)
    if ext == ".pdf":
        return _load_pdf(path)
    if ext == ".epub":
        return _load_epub(path)
    raise ValueError(f"不支持的格式：{ext}")


def _subprocess_load_entry() -> None:
    """Child-process entry: argv[1]=book path, argv[2]=json output path."""
    import sys

    path = Path(sys.argv[1])
    out = Path(sys.argv[2])
    try:
        doc = load_book(path)
        payload = {
            "ok": True,
            "title": doc.title,
            "pages": list(doc.pages),
            "source_path": str(doc.source_path),
        }
        out.write_bytes(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    except Exception as e:
        out.write_bytes(
            json.dumps(
                {"ok": False, "error": f"{type(e).__name__}: {e}"},
                ensure_ascii=False,
            ).encode("utf-8")
        )
        raise SystemExit(1) from e


def load_book_isolated(
    path: Path, *, timeout: Optional[float] = None
) -> BookDocument:
    """Load a book without holding the GIL in this process.

    EPUB/PDF parsing is CPU-heavy pure Python. Doing it in-process (even on a
    worker thread) stalls every other thread — including the federation I/O
    loop — and SSH tunnels drop. Light txt/md loads stay in-process.
    """
    import subprocess
    import sys

    path = path.resolve()
    ext = path.suffix.lower()
    if ext in {".txt", ".md"}:
        return load_book(path)
    if timeout is None:
        timeout = float(os.environ.get("SSHCHAT_LIBRARY_LOAD_TIMEOUT", "180"))
    timeout = max(5.0, float(timeout))
    root = str(Path(__file__).resolve().parent)
    env = os.environ.copy()
    prev = env.get("PYTHONPATH", "").strip()
    env["PYTHONPATH"] = root + (os.pathsep + prev if prev else "")
    with tempfile.TemporaryDirectory(prefix="sshchat-libload-") as td:
        out = Path(td) / "book.json"
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "from library import _subprocess_load_entry; "
                    "_subprocess_load_entry()",
                    str(path),
                    str(out),
                ],
                cwd=root,
                env=env,
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(
                f"解析图书超时（>{int(timeout)}s）：{path.name}"
            ) from exc
        if not out.is_file():
            err = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                err or f"图书解析子进程失败（exit {proc.returncode}）"
            )
        try:
            payload = json.loads(out.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"图书解析结果无效：{exc}") from exc
        if not isinstance(payload, dict) or not payload.get("ok"):
            raise RuntimeError(
                str((payload or {}).get("error") or "图书解析失败")
            )
        pages = payload.get("pages")
        if not isinstance(pages, list) or not pages:
            raise RuntimeError("图书解析结果无正文")
        title = str(payload.get("title") or path.stem)
        return BookDocument(
            title=title,
            pages=[str(p) for p in pages],
            source_path=path,
        )


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


def _wrap_page_lines_utf8_bytes(text: str, max_bytes: int) -> list[str]:
    max_bytes = max(24, int(max_bytes))
    lines: list[str] = []
    for paragraph in re.split(r"\n+", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        current: list[str] = []
        current_bytes = 0
        for ch in paragraph:
            ch_bytes = len(ch.encode("utf-8"))
            if current and current_bytes + ch_bytes > max_bytes:
                lines.append("".join(current))
                current = [ch]
                current_bytes = ch_bytes
            else:
                current.append(ch)
                current_bytes += ch_bytes
        if current:
            lines.append("".join(current))
    return lines or ["（空白页）"]


def wrap_page_lines(text: str, width: int = LIBRARY_WRAP_WIDTH) -> list[str]:
    text = (text or "").strip()
    if not text:
        return ["（空白页）"]
    if LIBRARY_WRAP_BYTES > 0:
        return _wrap_page_lines_utf8_bytes(text, LIBRARY_WRAP_BYTES)
    return textwrap.wrap(
        text,
        width=max(40, width),
        break_long_words=True,
        break_on_hyphens=False,
    )


def _normalize_user(name: str) -> str:
    return (name or "").strip().lower()


def bookmark_book_key(book_name: str) -> str:
    """Normalize a bookmark storage key to the bare filename.

    Federated reading used to store ``origin::filename``, which broke progress
    when the same nick opened the owner's local copy (bare filename). Progress
    is shared by filename across nodes for the same nick.
    """
    return bookmark_bare_name(book_name)


def bookmark_bare_name(book_name: str) -> str:
    """Strip optional ``origin::`` prefix and return a safe basename."""
    raw = str(book_name or "").strip()
    if not raw:
        return ""
    if "::" in raw and not raw.startswith("::"):
        _origin, name = raw.split("::", 1)
        name = Path(name.strip()).name
    else:
        name = Path(raw).name
    if not name or Path(name).name != name:
        return ""
    return name


def _bookmark_entry_ts(entry: dict[str, Any]) -> int:
    try:
        return int(entry.get("updated_ts") or 0)
    except (TypeError, ValueError):
        return 0


def _bookmark_entry_better(candidate: dict[str, Any], current: Optional[dict[str, Any]]) -> bool:
    """True if candidate should replace current (LWW, then higher page)."""
    if not isinstance(candidate, dict):
        return False
    if not isinstance(current, dict):
        return True
    c_ts = _bookmark_entry_ts(candidate)
    cur_ts = _bookmark_entry_ts(current)
    if c_ts != cur_ts:
        return c_ts > cur_ts
    if candidate.get("deleted") and not current.get("deleted"):
        return True
    if current.get("deleted") and not candidate.get("deleted"):
        return False
    try:
        return int(candidate.get("page") or 0) > int(current.get("page") or 0)
    except (TypeError, ValueError):
        return False


def canonicalize_bookmark_map(entries: dict[str, Any]) -> dict[str, Any]:
    """Collapse ``origin::file`` and ``file`` keys into bare filenames (LWW)."""
    out: dict[str, Any] = {}
    for raw_key, entry in (entries or {}).items():
        bare = bookmark_bare_name(str(raw_key))
        if not bare or not isinstance(entry, dict):
            continue
        try:
            ts = _bookmark_entry_ts(entry)
        except Exception:
            ts = 0
        if entry.get("deleted"):
            normalized = {"deleted": True, "updated_ts": ts, "page": 0}
        else:
            try:
                page = max(0, int(entry.get("page") or 0))
            except (TypeError, ValueError):
                continue
            normalized = {"page": page, "updated_ts": ts}
        if _bookmark_entry_better(normalized, out.get(bare)):
            out[bare] = normalized
    return out


def merge_bookmark_entries(
    local: dict[str, Any],
    remote: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Last-write-wins merge by updated_ts. Returns (merged, changed)."""
    base = canonicalize_bookmark_map(local)
    incoming = canonicalize_bookmark_map(remote)
    out = dict(base)
    changed = out != local  # true when migrating origin:: keys → bare names
    for key, remote_entry in incoming.items():
        if _bookmark_entry_better(remote_entry, out.get(key)):
            out[key] = dict(remote_entry)
            changed = True
    return out, changed


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

    def _user_books_locked(self, user_key: str) -> dict[str, Any]:
        assert self._cache is not None
        users = self._cache["users"]
        raw = users.get(user_key, {})
        if not isinstance(raw, dict):
            raw = {}
        canon = canonicalize_bookmark_map(raw)
        if canon != raw:
            if canon:
                users[user_key] = canon
            else:
                users.pop(user_key, None)
            self._save_locked()
        return users.get(user_key, {}) if canon else {}

    def get_page(self, user: str, book_name: str) -> Optional[int]:
        key = _normalize_user(user)
        book_key = bookmark_bare_name(book_name)
        if not key or not book_key:
            return None
        with self._lock:
            self._ensure_loaded_locked()
            user_books = self._user_books_locked(key)
            entry = user_books.get(book_key)
            if not isinstance(entry, dict) or entry.get("deleted"):
                return None
            try:
                page = int(entry.get("page", 0))
            except (TypeError, ValueError):
                return None
            return max(0, page)

    def set_page(self, user: str, book_name: str, page: int) -> dict[str, Any]:
        """Set bookmark under bare filename. Returns entry map for federation."""
        key = _normalize_user(user)
        book_key = bookmark_bare_name(book_name)
        if not key or not book_key:
            return {}
        page = max(0, int(page))
        entry = {"page": page, "updated_ts": int(time.time())}
        with self._lock:
            self._ensure_loaded_locked()
            assert self._cache is not None
            users = self._cache["users"]
            user_books = self._user_books_locked(key)
            if not isinstance(user_books, dict):
                user_books = {}
            # Drop legacy origin-prefixed aliases for this file.
            for alias in list(user_books.keys()):
                if alias != book_key and bookmark_bare_name(alias) == book_key:
                    del user_books[alias]
            user_books[book_key] = entry
            users[key] = user_books
            self._save_locked()
        return {book_key: dict(entry)}

    def list_for_user(self, user: str) -> dict[str, int]:
        key = _normalize_user(user)
        with self._lock:
            self._ensure_loaded_locked()
            raw = self._user_books_locked(key)
            out: dict[str, int] = {}
            for book_name, entry in raw.items():
                if isinstance(entry, dict) and not entry.get("deleted"):
                    try:
                        out[str(book_name)] = max(0, int(entry.get("page", 0)))
                    except (TypeError, ValueError):
                        continue
            return out

    def export_user(self, user: str) -> dict[str, Any]:
        """Full bookmark map for federation (includes tombstones; bare keys)."""
        key = _normalize_user(user)
        with self._lock:
            self._ensure_loaded_locked()
            raw = self._user_books_locked(key)
            out: dict[str, Any] = {}
            for book_name, entry in raw.items():
                bk = bookmark_bare_name(str(book_name))
                if not bk or not isinstance(entry, dict):
                    continue
                ts = _bookmark_entry_ts(entry)
                if entry.get("deleted"):
                    out[bk] = {"deleted": True, "updated_ts": ts, "page": 0}
                else:
                    try:
                        page = max(0, int(entry.get("page") or 0))
                    except (TypeError, ValueError):
                        continue
                    out[bk] = {"page": page, "updated_ts": ts}
            return out

    def merge_from_remote(self, user: str, entries: dict[str, Any]) -> bool:
        """Apply federated bookmark entries (LWW, bare filenames)."""
        key = _normalize_user(user)
        if not key or not isinstance(entries, dict) or not entries:
            return False
        with self._lock:
            self._ensure_loaded_locked()
            assert self._cache is not None
            users = self._cache["users"]
            local = users.get(key, {})
            if not isinstance(local, dict):
                local = {}
            merged, changed = merge_bookmark_entries(local, entries)
            if not changed:
                return False
            if merged:
                users[key] = merged
            else:
                users.pop(key, None)
            self._save_locked()
            return True

    def clear_book(self, user: str, book_name: str) -> Optional[dict[str, Any]]:
        """Clear a bookmark. Returns tombstone map for federation, or None."""
        key = _normalize_user(user)
        book_key = bookmark_bare_name(book_name)
        if not key or not book_key:
            return None
        with self._lock:
            self._ensure_loaded_locked()
            assert self._cache is not None
            user_books = self._user_books_locked(key)
            if not isinstance(user_books, dict):
                return None
            existing = user_books.get(book_key)
            if isinstance(existing, dict) and existing.get("deleted"):
                return None
            active = isinstance(existing, dict) and not existing.get("deleted")
            alias_active = any(
                k != book_key
                and bookmark_bare_name(k) == book_key
                and isinstance(user_books.get(k), dict)
                and not user_books[k].get("deleted")
                for k in user_books
            )
            if not active and not alias_active:
                return None
            entry = {"deleted": True, "updated_ts": int(time.time()), "page": 0}
            for alias in list(user_books.keys()):
                if bookmark_bare_name(alias) == book_key:
                    del user_books[alias]
            user_books[book_key] = entry
            self._cache["users"][key] = user_books
            self._save_locked()
            return {book_key: dict(entry)}
