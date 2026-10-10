package chat.ssh.sshchat.library

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class LibraryParserTest {

    /** Port of server `library._wrap_page_lines_utf8_bytes`. */
    private fun serverWrap(text: String, maxBytes: Int = 78): List<String> {
        val lines = mutableListOf<String>()
        for (raw in text.split(Regex("\n+"))) {
            val para = raw.trim()
            if (para.isEmpty()) continue
            val cur = StringBuilder()
            var curBytes = 0
            var i = 0
            while (i < para.length) {
                val cp = para.codePointAt(i)
                val ch = String(Character.toChars(cp))
                val b = ch.toByteArray(Charsets.UTF_8).size
                if (cur.isNotEmpty() && curBytes + b > maxBytes) {
                    lines += cur.toString()
                    cur.setLength(0)
                    curBytes = 0
                }
                cur.append(ch)
                curBytes += b
                i += Character.charCount(cp)
            }
            if (cur.isNotEmpty()) lines += cur.toString()
        }
        return lines
    }

    /** What the phone sees: `[*]    <chunk>` → star body `   <chunk>`, trailing spaces trimmed by SSH. */
    private fun feedPage(p: LibraryParser, title: String, page: Int, total: Int, text: String): LibraryPage {
        assertTrue(p.feed("--- 《$title》 第 $page/$total 页 ---").libraryLine)
        for (ln in serverWrap(text)) {
            val r = p.feed("   $ln".trimEnd())
            assertTrue(r.libraryLine)
            assertTrue(r.events.isEmpty())
        }
        val end = p.feed("翻页：/library next | prev | page <页码> | search <关键词> | show | info | close")
        return (end.events.single() as LibraryEvent.Page).page
    }

    @Test
    fun reflowRestoresChineseParagraphs() {
        val paras = listOf(
            "话说天下大势，分久必合，合久必分。周末七国分争，并入于秦。及秦灭之后，楚、汉分争，又并入于汉。汉朝自高祖斩白蛇而起义，一统天下。",
            "短段。",
            "后来光武中兴，传至献帝，遂分为三国。推其致乱之由，殆始于桓、灵二帝。桓帝禁锢善类，崇信宦官。",
        )
        val page = feedPage(LibraryParser(), "三国演义", 1, 120, paras.joinToString("\n"))
        assertEquals("三国演义", page.title)
        assertEquals(1, page.page)
        assertEquals(120, page.total)
        assertEquals(paras, page.paragraphs)
    }

    @Test
    fun reflowRestoresEnglishParagraphsAcrossTrimmedSpaces() {
        val paras = listOf(
            "It was the best of times, it was the worst of times, it was the age of wisdom, it was the age of foolishness, it was the epoch of belief, it was the epoch of incredulity.",
            "There were a king with a large jaw and a queen with a plain face, on the throne of England.",
        )
        val page = feedPage(LibraryParser(), "A Tale", 3, 9, paras.joinToString("\n"))
        assertEquals(paras, page.paragraphs)
    }

    @Test
    fun reflowMixedScript() {
        val paras = listOf(
            "第一章 Python 入门：Python 是一种解释型、面向对象、动态数据类型的高级程序设计语言，由 Guido van Rossum 于 1989 年底发明。",
            "Hello 世界。",
        )
        val page = feedPage(LibraryParser(), "教程", 2, 2, paras.joinToString("\n"))
        assertEquals(paras, page.paragraphs)
    }

    @Test
    fun catalogWithOriginsAndBookmarks() {
        val p = LibraryParser()
        val start = p.feed("--- 图书馆 ---")
        assertTrue(start.events.single() is LibraryEvent.CatalogStart)
        assertTrue(p.feed("联邦并集共 3 本（本机 2，对端 1）。").libraryLine)
        assertTrue(p.feed("1. [EPUB] 三国演义.epub (1.2 MB) · 书签第 12 页").libraryLine)
        assertTrue(p.feed("2. [TXT] notes (draft).txt (800 B)").libraryLine)
        assertTrue(p.feed("3. [PDF] 红楼梦.pdf (3.4 MB) @node-b").libraryLine)
        assertTrue(p.feed("打开：/library open <序号>  或  /library open <文件名[@节点]>").libraryLine)
        val end = p.feed("我的书签：/library bookmarks")
        val cat = end.events.single() as LibraryEvent.Catalog
        assertEquals(3, cat.books.size)
        assertEquals(LibraryBook(1, "EPUB", "三国演义.epub", "1.2 MB", null, 12), cat.books[0])
        assertEquals("notes (draft).txt", cat.books[1].name)
        assertNull(cat.books[1].bookmarkPage)
        assertEquals("node-b", cat.books[2].origin)
        assertEquals("红楼梦.pdf@node-b", cat.books[2].openToken)
        assertFalse(p.feed("普通系统消息").libraryLine)
    }

    @Test
    fun emptyCatalogEndsWithoutFooter() {
        val p = LibraryParser()
        p.feed("--- 图书馆 ---")
        p.feed("本机目录为空：/srv/books")
        val end = p.feed("联邦暂无共享图书；支持格式：.epub、.txt、.md、.pdf")
        val cat = end.events.single() as LibraryEvent.Catalog
        assertTrue(cat.books.isEmpty())
        assertEquals(2, cat.notes.size)
    }

    @Test
    fun flushShowsPartialCatalogOnly() {
        val p = LibraryParser()
        p.feed("--- 图书馆 ---")
        p.feed("1. [TXT] a.txt (1.0 KB)")
        assertEquals(1, (p.flush().single() as LibraryEvent.Catalog).books.size)
        assertTrue(p.flush().isEmpty())
        p.feed("--- 《a》 第 1/2 页 ---")
        p.feed("   正文")
        assertTrue(p.flush().isEmpty())
    }

    @Test
    fun searchResultsAndNotices() {
        val p = LibraryParser()
        assertTrue(p.feed("在《三国演义》中搜索「赤壁」，找到 2 处：").libraryLine)
        assertTrue(p.feed("  第 49 页：…周瑜纵火烧赤壁…").libraryLine)
        assertTrue(p.feed("  第 50 页：…赤壁之战…").libraryLine)
        val r = p.feed("用 /library page <页码> 跳转到对应页。")
        val res = r.events.single() as LibraryEvent.SearchResults
        assertEquals("赤壁", res.query)
        assertEquals(listOf(49, 50), res.hits.map { it.page })

        val none = p.feed("在《三国演义》中未找到「火星」。").events.single() as LibraryEvent.SearchResults
        assertTrue(none.hits.isEmpty())

        assertTrue(p.feed("已是最后一页。").events.single() is LibraryEvent.Notice)
        assertTrue(p.feed("请先用 /library open <序号> 打开图书。").events.single() is LibraryEvent.NeedsReopen)
        assertTrue(p.feed("已关闭当前图书（书签已保留）。").events.single() is LibraryEvent.Closed)
    }

    @Test
    fun pageWithoutFooterEndsOnNextNonBodyLine() {
        val p = LibraryParser()
        p.feed("--- 《a》 第 1/2 页 ---")
        p.feed("   第一段。")
        val r = p.feed("已是最后一页。")
        assertTrue(r.events[0] is LibraryEvent.Page)
        assertTrue(r.events[1] is LibraryEvent.Notice)
    }
}
