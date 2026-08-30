package chat.ssh.sshchat

/** Parse room / names / PM lines from server output. */
object ChatLineParsers {
    data class NamesLine(val room: String, val members: List<String>)
    data class PmLine(val from: String, val body: String)

    private val activeRoom = Regex("""Active room #([A-Za-z0-9_-]+)""", RegexOption.IGNORE_CASE)
    private val switchedTo = Regex("""Switched from #\S+ to #([A-Za-z0-9_-]+)""", RegexOption.IGNORE_CASE)
    private val namesLine = Regex("""^\[\*]\s+#([^\s(]+)\s+\(\d+\):\s*(.*)$""", RegexOption.IGNORE_CASE)
    private val pmLine = Regex("""^\[PM from ([^\]]+)]\s*(.*)$""", RegexOption.IGNORE_CASE)
    private val roomsList = Regex("""Rooms:\s*(.*)$""", RegexOption.IGNORE_CASE)
    private val roomToken = Regex("""\*?#([A-Za-z0-9_-]{1,32})""")

    fun parseActiveRoom(line: String): String? {
        val t = line.trim()
        return activeRoom.find(t)?.groupValues?.getOrNull(1)
            ?: switchedTo.find(t)?.groupValues?.getOrNull(1)
    }

    /** Parse `Rooms: #default, *#ops` (body or full system line). */
    fun parseRoomsList(line: String): List<String>? {
        val body = roomsList.find(line.trim())?.groupValues?.getOrNull(1) ?: return null
        val rooms = roomToken.findAll(body).map { it.groupValues[1] }.toList()
        return rooms.ifEmpty { null }
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
        val t = normalizeForParse(line)
        pmLine.matchEntire(t)?.let { m ->
            return PmLine(m.groupValues[1].trim(), m.groupValues[2])
        }
        val idx = t.indexOf("[PM from ", ignoreCase = true)
        if (idx >= 0) {
            pmLine.matchEntire(t.substring(idx))?.let { m ->
                return PmLine(m.groupValues[1].trim(), m.groupValues[2])
            }
        }
        return null
    }

    // Same shapes as client.py / electron (do not treat [#room] or [HH:MM:SS] as sender).
    private val roomChat = Regex("""^\[#([^\]]+)]\s+\[([^\]]+)]\s+(.*)$""")
    private val plainChat = Regex("""^\[([^\]]+)]\s+(.*)$""")
    /** PTY junk before the real `[#room] [nick] body` — take last match. */
    private val roomChatLoose = Regex("""\[#([^\]]+)]\s+\[([^\]]+)]\s+(.*)$""")
    private val timePrefix = Regex("""^(?:>?\s*)?(?:\[\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?]|\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?)\s+""")
    private val leadingGarbage = Regex("""^[\uFFFD\u25A1\uFEFF\u00A0\s]+""")
    /** Bare CSI fragment (params optional: `[K` as well as `[2K`). Not `[*]`/`[#`. */
    private const val bareCsiFragment = """(?:\[(?:\??(?:\d{1,4}(?:;\d{1,4})*)?)?[ABCDHJKSTfhlmnpqrstsu])"""
    /** CSI crumbs before [*] / [# when PTY mangles ESC → `?` (e.g. `?[2K`, bare `[2K` / `[K`). */
    private val ptyCrumbsBeforeTag =
        Regex("""^(?:(?:\?\[[0-9;?]*[@-~]?)|(?:\u001B\[[0-9;?]*[@-~]?)|$bareCsiFragment|[?\uFFFD0-9; \t])+(?=\[(?:\*|#))""")
    private val ptyNoiseOnlyPrefix =
        Regex("""^(?:(?:\?\[[0-9;?]*[@-~]?)|(?:\u001B\[[0-9;?]*[@-~]?)|$bareCsiFragment|[?\uFFFD0-9; \t])+$""")
    /** Bare CSI at line start when ESC/`?` was eaten (e.g. `[2K` / `[K` before `[*] 9 …). */
    private val bareCsiPrefix = Regex("""^(?:$bareCsiFragment)+""")
    private val systemSenders = setOf("+", "-", "*", "!")
    private val ignoredSenders = setOf("OK", "ERROR", "INFO", "WARN", "WARNING", "DEBUG", "HINT")

    data class ChatLine(val room: String?, val sender: String, val body: String)

    /** Strip local clock / prompt prefixes before parsing chat. */
    fun normalizeForParse(line: String): String {
        var t = line.trim()
        t = leadingGarbage.replace(t, "")
        while (true) {
            val nxt = timePrefix.replaceFirst(t, "").trimStart()
            if (nxt == t) break
            t = nxt
        }
        if (t.startsWith(">")) {
            t = t.dropWhile { it == '>' || it == ' ' }
        }
        while (true) {
            val nxt = ptyCrumbsBeforeTag.replaceFirst(t, "")
            if (nxt == t) break
            t = nxt
        }
        return t
    }

    /** Resolve nested clock / [#room] quirks (same as iOS). */
    private fun unwrapChat(chat: ChatLine): ChatLine {
        var sender = chat.sender
        var body = chat.body
        var room = chat.room
        if (sender.matches(Regex("""^\d{1,2}:\d{2}.*"""))) {
            parseChat(body)?.let { nested ->
                sender = nested.sender
                body = nested.body
                room = nested.room ?: room
            }
        }
        if (sender.startsWith("#")) {
            parseChat(body)?.let { nested ->
                sender = nested.sender
                body = nested.body
                room = nested.room ?: room
            }
        }
        return ChatLine(room, sender, body)
    }

    fun parseChat(line: String): ChatLine? {
        val t = normalizeForParse(line)
        roomChat.matchEntire(t)?.let { m ->
            return ChatLine(m.groupValues[1], m.groupValues[2], m.groupValues[3])
        }
        plainChat.matchEntire(t)?.let { m ->
            val sender = m.groupValues[1]
            if (sender.lowercase().startsWith("pm from ")) return null
            // CSI crumb + [*] → fake sender like "2K[*" / "K[*"; let board heuristics handle it.
            if ("*" in sender) return null
            if (clockSender.matches(sender)) return null
            return ChatLine(null, sender, m.groupValues[2])
        }
        roomChatLoose.find(t)?.let { m ->
            return ChatLine(m.groupValues[1], m.groupValues[2], m.groupValues[3])
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
        val unwrapped = unwrapChat(chat)
        var sender = unwrapped.sender
        var body = unwrapped.body
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

    /** UI classification for bubble / system / board cards. */
    sealed class DisplayKind {
        data class Bubble(
            val mine: Boolean,
            val room: String?,
            val sender: String,
            val body: String,
            val time: String,
        ) : DisplayKind()
        data class System(val text: String) : DisplayKind()
        data class BoardLine(val text: String) : DisplayKind()
    }

    private val clockCapture = Regex("""^>?\[?(\d{1,2}:\d{2}(?::\d{2})?)\]?\s+""")
    private val clockSender = Regex("""^\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?$""")

    /** client.py timestamps on wrapped `[*]` continuations (`[09:03:28] 续行` without `[*]`). */
    fun isClientClockContinuation(line: String): Boolean {
        val t = line.trim()
        if ("[*]" in t || "[#" in t) return false
        return clockCapture.containsMatchIn(t)
    }

    /** Prefer line clock (`[HH:MM:SS]`); else local now. */
    fun extractDisplayTime(line: String): String {
        clockCapture.find(line.trim())?.groupValues?.getOrNull(1)?.let { return it }
        val cal = java.util.Calendar.getInstance()
        return "%02d:%02d:%02d".format(
            cal.get(java.util.Calendar.HOUR_OF_DAY),
            cal.get(java.util.Calendar.MINUTE),
            cal.get(java.util.Calendar.SECOND),
        )
    }

    /** `[#room] [*] body` (server) or `[*] body` (client.py SSH display). */
    private val gameStarRoom = Regex("""^\[#[^\]]+\]\s+\[\*\](?: (.*))?$""")
    private val gameStarBare = Regex("""^\[\*\](?: (.*))?$""")

    /**
     * Body only (leading spaces kept). Null if not a game/system-star wire line.
     * Mobile SSH sessions run client.py, which rewrites `[#room] [*]` → `[*]`.
     */
    fun parseGameStarBody(line: String): String? {
        val t = normalizeForParse(line)
        matchGameStarBody(t)?.let { return it }
        val idx = t.indexOf("[*]")
        if (idx > 0) {
            val prefix = t.substring(0, idx)
            val rest = t.substring(idx)
            if (ptyNoiseOnlyPrefix.matches(prefix)) {
                return matchGameStarBody(rest)
            }
            matchGameStarBody(rest)?.let { body ->
                if (looksLikeGameBoardContent(body)) return body
            }
        }
        return null
    }

    private fun matchGameStarBody(t: String): String? {
        gameStarRoom.matchEntire(t)?.let { return it.groupValues.getOrNull(1) ?: "" }
        gameStarBare.matchEntire(t)?.let { return it.groupValues.getOrNull(1) ?: "" }
        return null
    }

    /** Board row text with `[*]` / PTY crumbs removed. */
    fun boardLineText(line: String): String {
        parseGameStarBody(line)?.let { return it }
        var t = normalizeForParse(line)
        while (true) {
            val nxt = bareCsiPrefix.replaceFirst(t, "")
            if (nxt == t) break
            t = nxt
        }
        val idx = t.indexOf("[*]")
        if (idx >= 0) {
            var after = t.substring(idx + 3)
            if (after.startsWith(" ")) after = after.substring(1)
            val prefix = t.substring(0, idx)
            if (prefix.isEmpty() || ptyNoiseOnlyPrefix.matches(prefix) || looksLikeGameBoardContent(after)) {
                return after.trimEnd()
            }
        }
        return t.trimEnd()
    }

    fun shouldContinueBoard(line: String): Boolean {
        if (parseGameStarBody(line) != null) return true
        val chat = parseChat(line)
        if (chat != null && chat.sender in systemSenders) return true
        if (looksLikeGameBoardContent(line)) return true
        if (chat != null && looksLikeGameBoardContent(chat.body)) return true
        return false
    }

    /** Board / game ASCII — must never become a WeChat bubble or centered system tip. */
    fun looksLikeGameBoardContent(payload: String): Boolean {
        val t = payload.trim()
        if (t.isEmpty()) return false
        if (t.any { it in "♔♕♖♗♘♙♚♛♜♝♞♟" }) return true
        if ("楚河汉界" in t || "图例：" in t || "请用等宽" in t || "己方在下方" in t) return true
        if ("←" in t && ("纵线" in t || "红方" in t || "黑方" in t || "白方" in t)) return true
        if (("-车" in t || "+车" in t || "-将" in t || "+帅" in t || "-马" in t || "+马" in t)) return true
        if (Regex("""^[+\-!·]""").containsMatchIn(t) && t.length > 6) return true
        if (Regex("""^\d{1,2}\s+(?:[.#o●○·]\s*){4,}""").containsMatchIn(t)) return true
        if (Regex("""^[a-h](?:\s+[a-h]){7}\s*$""", RegexOption.IGNORE_CASE).matches(t)) return true
        if (Regex("""^(?:\d{1,2}\s+){7,}\d{1,2}\s*$""").matches(t)) return true
        if (Regex("""^[一二三四五六七八九](?:\s+[一二三四五六七八九]){3,}""").containsMatchIn(t)) return true
        val keys = listOf(
            "轮到", "上一步", "对局", "gomoku", "chess", "xiangqi", "go ", "围棋",
            "五子棋", "中国象棋", "国际象棋", "斗兽棋", "积分=", "rating=", "W/L/D",
            "将军", "停一手", "落子", "走子", "行棋", "空席",
        )
        if (keys.any { it in t || it.lowercase() in t.lowercase() }) return true
        val dots = t.count { it == '·' || it == '.' }
        if (dots >= 8 && t.length < 140) return true
        val goish = t.count { it == '#' || it == 'o' || it == 'O' }
        if (goish >= 5 && dots >= 5) return true
        return false
    }

    fun classifyForDisplay(line: String, myName: String): DisplayKind {
        parsePm(line)?.let { pm ->
            return DisplayKind.Bubble(
                mine = false,
                room = null,
                sender = pm.from,
                body = pm.body,
                time = extractDisplayTime(line),
            )
        }
        parseGameStarBody(line)?.let { return DisplayKind.BoardLine(it) }

        val chat = parseChat(line) ?: run {
            val payload = boardLineText(line)
            if (looksLikeGameBoardContent(payload)) {
                return DisplayKind.BoardLine(payload)
            }
            if (normalizeForParse(line).contains("[*]") || line.contains("[*]")) {
                return DisplayKind.BoardLine(payload)
            }
            if (isClientClockContinuation(line)) {
                return DisplayKind.BoardLine(boardLineText(line))
            }
            return DisplayKind.System(normalizeForParse(line))
        }
        val unwrapped = unwrapChat(chat)
        val sender = unwrapped.sender
        val body = unwrapped.body
        val room = unwrapped.room
        if (sender == "*") {
            return DisplayKind.BoardLine(body)
        }
        if (sender in systemSenders || sender.uppercase() in ignoredSenders) {
            return DisplayKind.System(
                if (body.isNotEmpty()) "[$sender] $body" else line,
            )
        }
        val me = myName.trim()
        val mine = me.isNotEmpty() && sender.equals(me, ignoreCase = true)
        return DisplayKind.Bubble(mine, room, sender, body, extractDisplayTime(line))
    }
}
