package chat.ssh.sshchat.library

/**
 * Captures the `[*]` reply of a `/dict` sent from the reader so it can be shown in a popup
 * instead of the chat log. The server sends no end marker; callers finish after a quiet period.
 * Mirrors `server.py` `_handle_dict` / `dict_lookup.lookup_lines`.
 */
class DictCapture {
    private var word: String? = null
    private var started = false
    private val lines = mutableListOf<String>()

    val isActive: Boolean get() = word != null

    fun begin(word: String) {
        this.word = word
        started = false
        lines.clear()
    }

    fun cancel() {
        word = null
        started = false
        lines.clear()
    }

    /** @return true when the star body belongs to the pending lookup. */
    fun feed(body: String): Boolean {
        val w = word ?: return false
        val t = body.trim()
        if (header.matches(t)) {
            started = true
            lines += t
            return true
        }
        if (!started) {
            if (errorPrefixes.none { t.startsWith(it) }) return false
            started = true
            lines += t
            return true
        }
        if (body.isEmpty() || body.startsWith("  ") || t.startsWith("英 [") || t.startsWith("美 [") ||
            t.startsWith("[") || t.startsWith(w)
        ) {
            lines += body.trimEnd()
            return true
        }
        return false
    }

    /** End the lookup; null when nothing arrived yet. */
    fun finish(): List<String>? {
        if (!started) return null
        val out = lines.toList()
        cancel()
        return out
    }

    companion object {
        const val MAX_LEN = 64

        private val header = Regex("""^---\s*(英→中|中→英|汉语)：.*---$""")
        private val errorPrefixes = listOf(
            "词典查询失败", "请提供要查询的词语", "query too long", "missing word", "empty query",
        )
        private val modeAliases = setOf("en", "eng", "英", "ce", "cn", "中", "中英", "zh", "hh", "汉", "汉语")
        private val edge = Regex("""^[\s\p{P}\p{S}\u3000]+|[\s\p{P}\p{S}\u3000]+$""")

        /** Selected text → query word, or null when nothing usable is selected. */
        fun normalize(selected: String): String? =
            selected.replace(edge, "").replace(Regex("""\s+"""), " ").ifEmpty { null }

        fun hasCjk(s: String): Boolean = s.any { it in '\u4e00'..'\u9fff' }

        /**
         * Plain `/dict 词` gives 中→英 + 汉语 for Chinese; force a mode when the word itself
         * would be read as a mode alias or as `help`.
         */
        fun command(word: String): String {
            if (!hasCjk(word)) return "/dict en $word"
            val first = word.substringBefore(' ').lowercase()
            return if (first in modeAliases || word == "帮助") "/dict cn $word" else "/dict $word"
        }
    }
}
