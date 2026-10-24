package chat.ssh.sshchat.news

data class NewsItem(
    val id: String,
    val categoryLabel: String,
    val categoryToken: String,
    val index: Int,
    val source: String,
    val title: String,
    val body: String,
    val link: String,
)

sealed class NewsEvent {
    data class Items(val items: List<NewsItem>) : NewsEvent()
    data class Notice(val text: String) : NewsEvent()
}

/**
 * Turns server `/news` star-body lines into items. Mirrors Electron NewsPanel
 * plus an indented `http(s)://…` line for the original article URL.
 */
class NewsParser {
    data class Result(val newsLine: Boolean, val events: List<NewsEvent>) {
        companion object {
            val NONE = Result(false, emptyList())
        }
    }

    private var categoryLabel = ""
    private var categoryToken = "all"
    private var current: NewsItem? = null
    private val items = LinkedHashMap<String, NewsItem>()
    private var dirty = false

    fun reset() {
        categoryLabel = ""
        categoryToken = "all"
        current = null
        items.clear()
        dirty = false
    }

    fun flush(): List<NewsEvent> {
        commitCurrent()
        if (!dirty) return emptyList()
        dirty = false
        return listOf(NewsEvent.Items(items.values.toList()))
    }

    fun feed(body: String): Result {
        val t = body.trim()
        parseSection(t)?.let { label ->
            val ev = snapshot()
            categoryLabel = label
            categoryToken = inferCategoryToken(label)
            return Result(true, ev)
        }
        parseTitle(t)?.let { parsed ->
            val ev = snapshot()
            val token = categoryToken
            val label = categoryLabel.ifBlank { "新闻" }
            current = parsed.copy(
                id = "$token:${parsed.index}",
                categoryLabel = label,
                categoryToken = token,
            )
            dirty = true
            return Result(true, ev)
        }
        parseOriginalUrl(t)?.let { link ->
            if (current != null) {
                current = current!!.copy(link = link)
                dirty = true
                return Result(true, snapshot())
            }
        }
        if (current != null && t.isNotEmpty() && body.isNotEmpty() && body[0].isWhitespace()) {
            val prev = current!!.body
            current = current!!.copy(body = if (prev.isEmpty()) t else "$prev $t")
            dirty = true
            return Result(true, emptyList())
        }
        if (isNewsNotice(t)) {
            val ev = snapshot().toMutableList()
            ev += NewsEvent.Notice(t)
            return Result(true, ev)
        }
        return Result(false, emptyList())
    }

    private fun snapshot(): List<NewsEvent> {
        commitCurrent()
        if (!dirty && items.isEmpty()) return emptyList()
        dirty = false
        return listOf(NewsEvent.Items(items.values.toList()))
    }

    private fun commitCurrent() {
        val cur = current ?: return
        items[cur.id] = cur
        current = null
        dirty = true
    }

    companion object {
        private val SECTION = Regex("""^---\s*(.+?)\s*---$""")
        private val TITLE = Regex("""^(\d+)\.\s+\[([^\]]+)]\s+(.+)$""")
        private val URL = Regex("""^https?://\S+$""", RegexOption.IGNORE_CASE)
        private val NOTICE_PREFIXES = listOf(
            "可用：", "单条更长提要", "抓取网页正文", "暂时没有取到新闻",
            "Usage:", "Aliases:", "说明：", "例：", "序号须为",
        )

        fun inferCategoryToken(label: String): String {
            val l = label.lowercase()
            if ("中文" in l || "cn" in l || "chinese" in l) return "中文"
            if ("国际" in l || "world" in l || "intl" in l) return "国际"
            if ("科技" in l || "tech" in l) return "科技"
            return "all"
        }

        fun parseSection(t: String): String? {
            val m = SECTION.matchEntire(t) ?: return null
            val label = m.groupValues[1].trim()
            return if (looksLikeNewsSection(label)) label else null
        }

        fun parseTitle(t: String): NewsItem? {
            val m = TITLE.matchEntire(t) ?: return null
            val idx = m.groupValues[1].toIntOrNull() ?: return null
            return NewsItem(
                id = "$idx:${m.groupValues[3]}",
                categoryLabel = "",
                categoryToken = "all",
                index = idx,
                source = m.groupValues[2],
                title = m.groupValues[3],
                body = "",
                link = "",
            )
        }

        fun parseOriginalUrl(t: String): String? {
            val u = if (t.startsWith("news-link ", ignoreCase = true)) t.substring(10).trim() else t
            return u.takeIf { URL.matches(it) }
        }

        fun isOriginalUrl(t: String): Boolean = parseOriginalUrl(t) != null

        private fun looksLikeNewsSection(label: String): Boolean {
            val l = label.lowercase()
            if ("图书馆" in l || "library" in l) return false
            return "新闻" in l || "news" in l ||
                "中文" in l || "国际" in l || "科技" in l ||
                "world" in l || "tech" in l
        }

        private fun isNewsNotice(t: String): Boolean =
            NOTICE_PREFIXES.any { t.startsWith(it) }
    }
}
