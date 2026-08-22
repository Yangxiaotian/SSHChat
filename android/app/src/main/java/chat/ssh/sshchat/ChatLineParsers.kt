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
}
