package chat.ssh.sshchat

/** Parse room / names / PM lines from server output. */
object ChatLineParsers {
    data class NamesLine(val room: String, val members: List<String>)
    data class PmLine(val from: String, val body: String)

    private val activeRoom = Regex("""Active room #([A-Za-z0-9_-]+)""", RegexOption.IGNORE_CASE)
    private val switchedTo = Regex("""Switched from #\S+ to #([A-Za-z0-9_-]+)""", RegexOption.IGNORE_CASE)
    private val namesLine = Regex("""^\[\*]\s+#([^\s(]+)\s+\(\d+\):\s*(.*)$""", RegexOption.IGNORE_CASE)
    private val pmLine = Regex("""^\[PM from ([^\]]+)]\s*(.*)$""", RegexOption.IGNORE_CASE)

    fun parseActiveRoom(line: String): String? {
        val t = line.trim()
        return activeRoom.find(t)?.groupValues?.getOrNull(1)
            ?: switchedTo.find(t)?.groupValues?.getOrNull(1)
    }

    fun parseNames(line: String): NamesLine? {
        val t = line.trim()
        val m = namesLine.matchEntire(t) ?: return null
        val room = m.groupValues[1].trim()
        val tail = m.groupValues[2].trim()
        if (tail.equals("(empty)", ignoreCase = true) || tail.isEmpty()) {
            return NamesLine(room, emptyList())
        }
        val members = tail.split(',').map { it.trim() }.filter { it.isNotEmpty() }
        return NamesLine(room, members)
    }

    fun parsePm(line: String): PmLine? {
        val t = line.trim()
        val m = pmLine.matchEntire(t) ?: return null
        return PmLine(m.groupValues[1].trim(), m.groupValues[2])
    }

    // Same shapes as client.py / electron (do not treat [#room] or [HH:MM:SS] as sender).
    private val roomChat = Regex("""^\[#([^\]]+)]\s+\[([^\]]+)] (.*)$""")
    private val plainChat = Regex("""^\[([^\]]+)] (.*)$""")
    private val timePrefix = Regex("""^>?\[\d{1,2}:\d{2}(?::\d{2})?]\s*""")
    private val systemSenders = setOf("+", "-", "*", "!")
    private val ignoredSenders = setOf("OK", "ERROR", "INFO", "WARN", "WARNING", "DEBUG", "HINT")

    data class ChatLine(val room: String?, val sender: String, val body: String)

    /** Strip local clock / prompt prefixes before parsing chat. */
    fun normalizeForParse(line: String): String {
        var t = line.trim()
        while (true) {
            val nxt = timePrefix.replaceFirst(t, "").trimStart()
            if (nxt == t) break
            t = nxt
        }
        if (t.startsWith(">")) {
            t = t.dropWhile { it == '>' || it == ' ' }
        }
        return t
    }

    fun parseChat(line: String): ChatLine? {
        val t = normalizeForParse(line)
        roomChat.matchEntire(t)?.let { m ->
            return ChatLine(m.groupValues[1], m.groupValues[2], m.groupValues[3])
        }
        plainChat.matchEntire(t)?.let { m ->
            val sender = m.groupValues[1]
            if (sender.lowercase().startsWith("pm from ")) return null
            return ChatLine(null, sender, m.groupValues[2])
        }
        return null
    }

    /** Whether an incoming line should trigger a receive chime (peer / PM / join-leave). */
    fun shouldAlert(
        line: String,
        myName: String,
        recentOutboundBody: String = "",
        recentOutboundAtMs: Long = 0L,
    ): Boolean {
        val t = normalizeForParse(line)
        if (t.isEmpty()) return false
        if (parsePm(t) != null) return true

        val chat = parseChat(t)
        if (chat == null) {
            val lower = t.lowercase()
            return " joined " in lower || " left " in lower
        }
        var sender = chat.sender
        var body = chat.body
        // Leftover clock parsed as sender → real chat is in the body.
        if (sender.matches(Regex("""^\\d{1,2}:\\d{2}.*"""))) {
            val nested = parseChat(body) ?: return false
            sender = nested.sender
            body = nested.body
        }
        // `[#room]` eaten as plain sender → body is `[nick] text`.
        if (sender.startsWith("#")) {
            val nested = parseChat(body) ?: return false
            sender = nested.sender
            body = nested.body
        }
        if (sender.uppercase() in ignoredSenders) return false
        if (sender in systemSenders) {
            if (sender == "+" || sender == "-") return true
            if (sender == "!") {
                val lower = body.lowercase()
                return " joined " in lower || " left " in lower
            }
            return false
        }
        val me = myName.trim()
        if (me.isNotEmpty() && sender.equals(me, ignoreCase = true)) return false
        val recent = recentOutboundBody.trim()
        // Exact echo only — hasSuffix would suppress peer "你好" after we sent "好".
        if (recent.isNotEmpty() &&
            System.currentTimeMillis() - recentOutboundAtMs < 4_000L &&
            body == recent
        ) {
            return false
        }
        return true
    }
}
