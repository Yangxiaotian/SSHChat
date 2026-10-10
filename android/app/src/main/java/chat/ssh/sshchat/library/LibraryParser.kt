package chat.ssh.sshchat.library

data class LibraryBook(
    val index: Int,
    val format: String,
    val name: String,
    val size: String,
    val origin: String?,
    /** 1-based page from `· 书签第 N 页`. */
    val bookmarkPage: Int?,
) {
    /** Token for `/library open`, stable across catalog re-ordering. */
    val openToken: String get() = if (origin.isNullOrBlank()) name else "$name@$origin"
}

data class LibraryPage(
    val title: String,
    /** 1-based. */
    val page: Int,
    val total: Int,
    val paragraphs: List<String>,
)

data class LibrarySearchHit(val page: Int, val snippet: String)

sealed class LibraryEvent {
    object CatalogStart : LibraryEvent()
    data class Catalog(val books: List<LibraryBook>, val notes: List<String>) : LibraryEvent()
    data class PageStart(val title: String, val page: Int, val total: Int) : LibraryEvent()
    data class Page(val page: LibraryPage) : LibraryEvent()
    data class SearchResults(val title: String, val query: String, val hits: List<LibrarySearchHit>) : LibraryEvent()
    data class Notice(val text: String) : LibraryEvent()
    data class Loading(val text: String) : LibraryEvent()
    data class Error(val text: String) : LibraryEvent()
    object Closed : LibraryEvent()
    object NeedsReopen : LibraryEvent()
}

/**
 * Turns server `/library` star lines (body after `[*]`) into structured events.
 * Mirrors `server.py` `_send_library_catalog` / `_send_library_page_payload` /
 * `_emit_library_search_hits`.
 */
class LibraryParser {
    data class Result(val libraryLine: Boolean, val events: List<LibraryEvent>) {
        companion object {
            val NONE = Result(false, emptyList())
        }
    }

    private enum class Section { NONE, CATALOG, PAGE, SEARCH }

    private var section = Section.NONE
    private val books = mutableListOf<LibraryBook>()
    private val notes = mutableListOf<String>()
    private var catalogDirty = false
    private var pageTitle = ""
    private var pageNo = 0
    private var pageTotal = 0
    private val pageLines = mutableListOf<String>()
    private var searchTitle = ""
    private var searchQuery = ""
    private val hits = mutableListOf<LibrarySearchHit>()

    fun reset() {
        section = Section.NONE
        books.clear()
        notes.clear()
        catalogDirty = false
        pageLines.clear()
        hits.clear()
    }

    /**
     * Show catalog rows received so far after a quiet period (the footer may be late or lost).
     * Pages and search results always end with a footer line, so they are never cut short here.
     */
    fun flush(): List<LibraryEvent> {
        if (section != Section.CATALOG || !catalogDirty) return emptyList()
        catalogDirty = false
        return listOf(catalogEvent())
    }

    fun feed(body: String): Result {
        val t = body.trim()
        val pre = mutableListOf<LibraryEvent>()
        when (section) {
            Section.PAGE -> {
                if (t.startsWith("翻页：") || t.startsWith("翻页:")) {
                    section = Section.NONE
                    return Result(true, listOf(pageEvent()))
                }
                if (body.startsWith("  ") && t.isNotEmpty()) {
                    pageLines += body.removePrefix(PAGE_INDENT)
                    return Result(true, emptyList())
                }
                pre += pageEvent()
                section = Section.NONE
            }
            Section.CATALOG -> {
                parseBook(t)?.let {
                    books += it
                    catalogDirty = true
                    return Result(true, emptyList())
                }
                if (CATALOG_NOTE_PREFIXES.any { t.startsWith(it) }) {
                    notes += t
                    catalogDirty = true
                    if (CATALOG_TERMINAL_PREFIXES.any { t.startsWith(it) }) {
                        section = Section.NONE
                        catalogDirty = false
                        return Result(true, listOf(catalogEvent()))
                    }
                    return Result(true, emptyList())
                }
                if (t.startsWith("打开：/library") || t.startsWith("查找：/library")) {
                    return Result(true, emptyList())
                }
                if (t.startsWith("我的书签：/library")) {
                    section = Section.NONE
                    catalogDirty = false
                    return Result(true, listOf(catalogEvent()))
                }
                if (catalogDirty) pre += catalogEvent()
                catalogDirty = false
                section = Section.NONE
            }
            Section.SEARCH -> {
                SEARCH_HIT.matchEntire(t)?.let { m ->
                    hits += LibrarySearchHit(m.groupValues[1].toInt(), m.groupValues[2].trim())
                    return Result(true, emptyList())
                }
                if (t.startsWith("用 /library page")) {
                    section = Section.NONE
                    return Result(true, listOf(searchEvent()))
                }
                pre += searchEvent()
                section = Section.NONE
            }
            Section.NONE -> Unit
        }

        val r = feedIdle(t)
        return if (pre.isEmpty()) r else Result(r.libraryLine, pre + r.events)
    }

    private fun feedIdle(t: String): Result {
        if (CATALOG_HEADER.matches(t)) {
            section = Section.CATALOG
            books.clear()
            notes.clear()
            catalogDirty = false
            return Result(true, listOf(LibraryEvent.CatalogStart))
        }
        PAGE_HEADER.matchEntire(t)?.let { m ->
            section = Section.PAGE
            pageTitle = m.groupValues[1]
            pageNo = m.groupValues[2].toInt()
            pageTotal = m.groupValues[3].toInt()
            pageLines.clear()
            return Result(true, listOf(LibraryEvent.PageStart(pageTitle, pageNo, pageTotal)))
        }
        SEARCH_HEADER.matchEntire(t)?.let { m ->
            section = Section.SEARCH
            searchTitle = m.groupValues[1]
            searchQuery = m.groupValues[2]
            hits.clear()
            return Result(true, emptyList())
        }
        SEARCH_NONE.matchEntire(t)?.let { m ->
            return Result(true, listOf(LibraryEvent.SearchResults(m.groupValues[1], m.groupValues[2], emptyList())))
        }
        if (t.startsWith("已自动跳转到第")) return Result(true, listOf(LibraryEvent.Notice(t)))
        if (t == "已是最后一页。" || t == "已是第一页。") return Result(true, listOf(LibraryEvent.Notice(t)))
        if (t.startsWith("已关闭当前图书")) return Result(true, listOf(LibraryEvent.Closed))
        if (t.startsWith("请先用 /library open")) return Result(true, listOf(LibraryEvent.NeedsReopen))
        if (t.startsWith("正在从节点") || t.startsWith("正在节点")) {
            return Result(true, listOf(LibraryEvent.Loading(t)))
        }
        if (ERROR_PREFIXES.any { t.startsWith(it) }) return Result(true, listOf(LibraryEvent.Error(t)))
        if (t.startsWith("用 /library 查看可用序号")) return Result(true, emptyList())
        return Result.NONE
    }

    private fun catalogEvent() = LibraryEvent.Catalog(books.toList(), notes.toList())

    private fun pageEvent() = LibraryEvent.Page(
        LibraryPage(pageTitle, pageNo, pageTotal, reflow(pageLines)),
    )

    private fun searchEvent() = LibraryEvent.SearchResults(searchTitle, searchQuery, hits.toList())

    companion object {
        /** `[*]    text` → star body `   text`. */
        private const val PAGE_INDENT = "   "

        /** server `library.LIBRARY_WRAP_BYTES` default. */
        const val DEFAULT_WRAP_BYTES = 78

        private val CATALOG_HEADER = Regex("""^---\s*图书馆\s*---$""")
        private val PAGE_HEADER = Regex("""^---\s*《(.+)》\s*第\s*(\d+)\s*/\s*(\d+)\s*页\s*---$""")
        private val BOOK_LINE = Regex(
            """^(\d+)\.\s+\[([A-Za-z0-9]+)]\s+(.+?)\s+\(([\d.]+\s*[KMGT]?B)\)(?:\s+@(\S+))?(?:\s+·\s+书签第\s*(\d+)\s*页)?$""",
        )
        private val SEARCH_HEADER = Regex("""^在《(.+)》中搜索「(.+)」，找到\s*\d+\s*处：$""")
        private val SEARCH_NONE = Regex("""^在《(.+)》中未找到「(.+)」。$""")
        private val SEARCH_HIT = Regex("""^第\s*(\d+)\s*页：(.*)$""")
        private val CATALOG_NOTE_PREFIXES = listOf(
            "联邦并集共", "查找「", "未找到匹配的图书", "用 /library 查看全部书目",
            "本机图书馆目录不存在", "本机目录为空", "联邦暂无共享图书",
        )
        private val CATALOG_TERMINAL_PREFIXES = listOf("用 /library 查看全部书目", "联邦暂无共享图书")
        private val ERROR_PREFIXES = listOf(
            "无法读取图书", "无法读取当前图书", "打开失败", "检索失败", "跳转失败",
            "未找到图书：", "无效页码", "页码须为整数",
        )

        fun parseBook(t: String): LibraryBook? {
            val m = BOOK_LINE.matchEntire(t) ?: return null
            return LibraryBook(
                index = m.groupValues[1].toInt(),
                format = m.groupValues[2].uppercase(),
                name = m.groupValues[3],
                size = m.groupValues[4],
                origin = m.groupValues[5].ifBlank { null },
                bookmarkPage = m.groupValues[6].toIntOrNull(),
            )
        }

        private fun utf8Len(s: String): Int {
            var n = 0
            var i = 0
            while (i < s.length) {
                val cp = s.codePointAt(i)
                n += when {
                    cp < 0x80 -> 1
                    cp < 0x800 -> 2
                    cp < 0x10000 -> 3
                    else -> 4
                }
                i += Character.charCount(cp)
            }
            return n
        }

        private fun firstCodePoint(s: String): String =
            if (s.isEmpty()) "" else s.substring(0, Character.charCount(s.codePointAt(0)))

        private fun isWordChar(c: Char): Boolean = c.code < 0x2E80 && c.isLetterOrDigit()

        /**
         * The server hard-wraps each paragraph into chunks of at most [wrapBytes] UTF-8 bytes
         * and drops blank lines. A chunk was force-cut iff appending the next chunk's first
         * character would have exceeded the budget; otherwise it ended a paragraph.
         * SSH line handling trims trailing spaces, so a cut right after a space between two
         * Latin words shows up as one byte short. Paragraphs are stripped server-side, so a
         * chunk that starts with a space is always a continuation.
         */
        fun reflow(lines: List<String>, wrapBytes: Int = DEFAULT_WRAP_BYTES): List<String> {
            val clean = lines.map { it.trimEnd() }.filter { it.isNotEmpty() }
            if (clean.isEmpty()) return emptyList()
            val observedMax = clean.maxOf { utf8Len(it) }
            val budget = if (observedMax > wrapBytes) observedMax else wrapBytes
            val out = mutableListOf<String>()
            val cur = StringBuilder(clean[0])
            var curLast = clean[0]
            for (i in 1 until clean.size) {
                val next = clean[i]
                val lastLen = utf8Len(curLast)
                val nextFirst = firstCodePoint(next.trimStart())
                val firstLen = utf8Len(nextFirst)
                val prevChar = curLast.last()
                val nextChar = next.trimStart().firstOrNull() ?: ' '
                when {
                    next.startsWith(" ") -> cur.append(next)
                    lastLen + firstLen > budget -> cur.append(next)
                    lastLen + 1 + firstLen > budget && isWordChar(prevChar) && isWordChar(nextChar) ->
                        cur.append(' ').append(next)
                    else -> {
                        out += cur.toString().trim()
                        cur.setLength(0)
                        cur.append(next)
                    }
                }
                curLast = next
            }
            out += cur.toString().trim()
            return out.filter { it.isNotEmpty() }
        }
    }
}
