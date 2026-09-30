package chat.ssh.sshchat.games

enum class GameKind(val id: String, val title: String) {
    XIANGQI("xiangqi", "中国象棋"),
    CHESS("chess", "国际象棋"),
    GO("go", "围棋"),
    GOMOKU("gomoku", "五子棋"),
    DOUSHOU("doushou", "斗兽棋"),
    JUNQI("junqi", "军棋"),
    DARKCHESS("darkchess", "暗棋"),
    REVERSI("reversi", "黑白棋"),
    BATTLESHIP("battleship", "海战棋"),
    HOLDEM("holdem", "德州扑克"),
    ZJH("zjh", "炸金花"),
    NIUTOU("niutou", "谁是牛头王"),
    MAHJONG("mahjong", "麻将"),
    SANGUO("sanguo", "三国杀"),
    WEREWOLF("werewolf", "狼人杀"),
}

/** 棋盘快照：row 0 对应服务端文本棋盘第一行。 */
class Board(
    val rows: Int,
    val cols: Int,
    val cells: List<String>,
    val last: Set<Int> = emptySet(),
    val rowLabels: List<Int> = emptyList(),
    val colLabels: List<String> = emptyList(),
) {
    fun at(r: Int, c: Int): String = cells[r * cols + c]
    fun isLast(r: Int, c: Int): Boolean = (r * cols + c) in last
}

data class Seat(
    val index: Int,
    val name: String,
    val detail: String,
    val active: Boolean = false,
    val alive: Boolean = true,
    val hp: Int? = null,
    val maxHp: Int? = null,
)

data class GameSnapshot(
    val kind: GameKind,
    val state: String,
    val header: String,
    val board: Board? = null,
    val board2: Board? = null,
    val flipped: Boolean = false,
    val players: Map<String, String> = emptyMap(),
    val turnSide: String? = null,
    val turnName: String? = null,
    val info: List<String> = emptyList(),
    val seats: List<Seat> = emptyList(),
    val hand: List<String> = emptyList(),
    val community: List<String> = emptyList(),
    val rows: List<Pair<List<Int>, Int>> = emptyList(),
    val fields: Map<String, String> = emptyMap(),
    val hints: List<String> = emptyList(),
    val messages: List<String> = emptyList(),
) {
    val isPlaying: Boolean
        get() {
            val s = state.lowercase()
            return s == "playing" || s == "night" || s == "day" ||
                "进行" in s || "in progress" in s || "选行" in s || "row pick" in s
        }
    val isWaiting: Boolean
        get() {
            val s = state.lowercase()
            return s == "waiting" || "等待开始" in s || "waiting to start" in s
        }
    val isSetup: Boolean get() = state.equals("setup", ignoreCase = true)
    val isEnded: Boolean
        get() {
            val s = state.lowercase()
            return s == "ended" || "结束" in s
        }
}

object GameParser {
    private data class Header(val kind: GameKind, val regex: Regex)

    private val headers = listOf(
        Header(GameKind.XIANGQI, Regex("""^xiangqi (?:对局)?[（(]([^）)]*)[）)]""")),
        Header(GameKind.CHESS, Regex("""^chess (?:对局)?[（(]([^）)]*)[）)]""")),
        Header(GameKind.GO, Regex("""^go (?:对局)?[（(]([^）)]*)[）)]""")),
        Header(GameKind.GOMOKU, Regex("""^gomoku (?:对局)?[（(]([^）)]*)[）)]""")),
        Header(GameKind.DOUSHOU, Regex("""^doushou (?:对局)?[（(]([^）)]*)[）)]""")),
        Header(GameKind.DARKCHESS, Regex("""^darkchess (?:对局)?[（(]([^）)]*)[）)]""")),
        Header(GameKind.REVERSI, Regex("""^reversi game [（(]([^）)]*)[）)]""")),
        Header(GameKind.BATTLESHIP, Regex("""^battleship game [（(]([^）)]*)[）)]""")),
        Header(GameKind.JUNQI, Regex("""^junqi game [（(]([^）)]*)[）)]""")),
        Header(GameKind.HOLDEM, Regex("""^(?:德州扑克 状态|Hold'em status)[：:]\s*(.+)$""")),
        Header(GameKind.ZJH, Regex("""^(?:炸金花 状态|Zha Jin Hua status)[：:]\s*(.+)$""")),
        Header(GameKind.NIUTOU, Regex("""^(?:牛头王 状态|6 Nimmt! status)[：:]\s*(.+)$""")),
        Header(GameKind.MAHJONG, Regex("""^(?:麻将 状态|Mahjong status)[：:]\s*(.+)$""")),
        Header(GameKind.SANGUO, Regex("""^三国杀·军争\s+(\S+)""")),
        Header(GameKind.WEREWOLF, Regex("""^werewolf state:\s*(\S+)""")),
        Header(GameKind.WEREWOLF, Regex("""^Werewolf started\. (Night) \d+""")),
        Header(GameKind.WEREWOLF, Regex("""^\S+ joined werewolf \(\d+ players\)""")),
    )

    fun detectHeader(line: String): Pair<GameKind, String>? {
        val t = line.trim()
        if (t.isEmpty()) return null
        for (h in headers) {
            val m = h.regex.find(t) ?: continue
            val state = m.groupValues.getOrNull(1)?.trim().orEmpty()
            return h.kind to when {
                h.kind == GameKind.WEREWOLF && state == "Night" -> "night"
                h.kind == GameKind.WEREWOLF && state.isEmpty() -> "waiting"
                else -> state
            }
        }
        return null
    }

    fun parseLatest(lines: List<String>, myName: String): GameSnapshot? {
        var idx = -1
        var head: Pair<GameKind, String>? = null
        for (i in lines.indices.reversed()) {
            val h = detectHeader(lines[i]) ?: continue
            idx = i
            head = h
            break
        }
        if (head == null) return null
        return try {
            parseBlock(head.first, head.second, lines.subList(idx, lines.size), myName)
        } catch (_: RuntimeException) {
            null
        }
    }

    fun parseBlock(kind: GameKind, state: String, block: List<String>, myName: String): GameSnapshot? =
        when (kind) {
            GameKind.XIANGQI -> parseXiangqi(state, block)
            GameKind.CHESS -> parseChess(state, block)
            GameKind.GO, GameKind.GOMOKU -> parseStones(kind, state, block)
            GameKind.REVERSI -> parseReversi(state, block)
            GameKind.DARKCHESS -> parseDarkchess(state, block)
            GameKind.DOUSHOU -> parseDoushou(state, block)
            GameKind.JUNQI -> parseJunqi(state, block)
            GameKind.BATTLESHIP -> parseBattleship(state, block)
            GameKind.HOLDEM, GameKind.ZJH -> parsePoker(kind, state, block)
            GameKind.NIUTOU -> parseNiutou(state, block, myName)
            GameKind.MAHJONG -> parseMahjong(state, block)
            GameKind.SANGUO -> parseSanguo(state, block)
            GameKind.WEREWOLF -> parseWerewolf(state, block)
        }

    // ---------- shared ----------

    private val rowLine = Regex("""^\s*(\d{1,2})\s(.*)$""")
    private val playerTag = Regex("""(红|黑|白|蓝|Red|Black|White|Blue)[：:]\s*([^\s（(]+)""")
    private val numberedPlayer = Regex("""(?:玩家|Player )([12])[：:]\s*([^\s（(]+)""")
    private val sideWords = mapOf(
        "红" to "red", "红方" to "red", "Red" to "red",
        "黑" to "black", "黑方" to "black", "Black" to "black",
        "白" to "white", "白方" to "white", "White" to "white",
        "蓝" to "blue", "Blue" to "blue",
    )

    private fun headerPlayers(header: String): Map<String, String> {
        val out = linkedMapOf<String, String>()
        for (m in playerTag.findAll(header)) {
            val side = sideWords[m.groupValues[1]] ?: continue
            val name = m.groupValues[2]
            if (name != "empty" && name != "空") out.putIfAbsent(side, name)
        }
        for (m in numberedPlayer.findAll(header)) {
            out.putIfAbsent("p${m.groupValues[1]}", m.groupValues[2])
        }
        return out
    }

    private val turnPatterns = listOf(
        Regex("""轮到\s*(红方|黑方|白方)\s*([^\s（(]+)"""),
        Regex("""^(Red|Black|White)\s+(\S+)\s+(?:to move|行棋)"""),
        Regex("""^Turn:\s*(Black|White)\s+(\S+)"""),
        Regex("""^Turn:\s*()([^\s(]+)"""),
        Regex("""轮到\s*#\d+\s*()(\S+)\s*的回合"""),
        Regex("""当前回合[：:]\s*#\d+\s*()(\S+)"""),
        Regex("""轮到[：:]\s*()([^\s（(，,]+)"""),
        Regex("""当前轮到[：:]\s*()(\S+)"""),
    )

    private fun findTurn(lines: List<String>): Pair<String?, String?> {
        for (i in lines.indices.reversed()) {
            val t = lines[i].trim()
            for (p in turnPatterns) {
                val m = p.find(t) ?: continue
                return sideWords[m.groupValues[1]] to m.groupValues[2].ifEmpty { null }
            }
        }
        return null to null
    }

    private fun lastLineMatching(lines: List<String>, re: Regex): MatchResult? {
        for (i in lines.indices.reversed()) re.find(lines[i].trim())?.let { return it }
        return null
    }

    private fun messagesAfter(block: List<String>, lastUsed: Int): List<String> =
        block.drop(lastUsed + 1).map { it.trim() }.filter { it.isNotEmpty() }.takeLast(4)

    private fun lastTurnIndex(block: List<String>, fallback: Int): Int {
        var best = fallback
        for (i in (fallback + 1) until block.size) {
            val t = block[i].trim()
            if (turnPatterns.any { it.containsMatchIn(t) } ||
                t.startsWith("上一步") || t.startsWith("Last move") ||
                t.startsWith("图例") || t.startsWith("Legend") ||
                t.startsWith("  ") || t.isEmpty() || block[i].startsWith(" ")
            ) {
                best = i
            } else break
        }
        return best
    }

    private data class Rows(val rows: List<List<String>>, val labels: List<Int>, val lastIdx: Int)

    private fun collectRows(
        block: List<String>,
        cols: Int,
        maxRows: Int,
        token: Regex,
        numbered: Boolean = true,
        tokenCount: Int = cols,
    ): Rows {
        val rows = mutableListOf<List<String>>()
        val labels = mutableListOf<Int>()
        var lastIdx = 0
        for ((i, raw) in block.withIndex()) {
            if (i == 0 || rows.size >= maxRows) continue
            var label = 0
            val rest = if (numbered) {
                val m = rowLine.find(raw) ?: continue
                label = m.groupValues[1].toInt()
                m.groupValues[2]
            } else {
                raw
            }
            val toks = token.findAll(rest).map { it.value }.toList()
            if (toks.size != tokenCount) continue
            rows += toks
            labels += label
            lastIdx = i
        }
        return Rows(rows, labels, lastIdx)
    }

    // ---------- xiangqi ----------

    private val xqToken = Regex("""[+\-!][^\s·*+\-!]|·|\*""")
    private val xqRedOnly = "帅仕相兵"
    private val xqBlackOnly = "将士象卒"

    private fun xqChar(letter: Char, red: Boolean): Char = when (letter) {
        'R' -> '车'
        'H', 'N' -> '马'
        'E', 'B' -> if (red) '相' else '象'
        'A' -> if (red) '仕' else '士'
        'G', 'K' -> if (red) '帅' else '将'
        'C' -> '炮'
        'S', 'P' -> if (red) '兵' else '卒'
        else -> letter
    }

    private fun parseXiangqi(state: String, block: List<String>): GameSnapshot? {
        val rows = collectRows(block, 9, 10, xqToken, numbered = false)
        if (rows.rows.size != 10) return null
        val flipped = block.any { "己方在下方" in it || "you are at the bottom" in it } ||
            block.drop(1).firstOrNull { it.isNotBlank() }?.trim()?.startsWith("一") == true
        val (turnSide, turnName) = findTurn(block)
        val moverRed = when (turnSide) {
            "red" -> false
            "black" -> true
            else -> true
        }
        val cells = mutableListOf<String>()
        val last = mutableSetOf<Int>()
        rows.rows.forEachIndexed { r, toks ->
            toks.forEachIndexed { c, tok ->
                val i = r * 9 + c
                when {
                    tok == "·" -> cells += ""
                    tok == "*" -> {
                        cells += ""
                        last += i
                    }
                    else -> {
                        val sym = tok[1]
                        val red = when (tok[0]) {
                            '+' -> true
                            '-' -> false
                            else -> when (sym) {
                                in xqRedOnly -> true
                                in xqBlackOnly -> false
                                else -> moverRed
                            }
                        }
                        if (tok[0] == '!') last += i
                        val ch = if (sym in 'A'..'Z') xqChar(sym, red) else sym
                        cells += (if (red) "r" else "b") + ch
                    }
                }
            }
        }
        val info = listOfNotNull(lastLineMatching(block, Regex("""^(上一步|Last move)[：:].*"""))?.value)
        return GameSnapshot(
            kind = GameKind.XIANGQI, state = state, header = block[0].trim(),
            board = Board(10, 9, cells, last), flipped = flipped,
            players = headerPlayers(block[0]), turnSide = turnSide, turnName = turnName, info = info,
            messages = messagesAfter(block, lastTurnIndex(block, rows.lastIdx)),
        )
    }

    // ---------- chess ----------

    private val chessToken = Regex("""\((.)\)|([♔♕♖♗♘♙♚♛♜♝♞♟·])""")
    private val chessFiles = Regex("""^\s*([a-h])(?:\s+[a-h]){7}\s*$""")

    private fun parseChess(state: String, block: List<String>): GameSnapshot? {
        val rows = mutableListOf<List<Pair<String, Boolean>>>()
        val ranks = mutableListOf<Int>()
        var files: List<String> = emptyList()
        var lastIdx = 0
        for ((i, raw) in block.withIndex()) {
            if (i == 0) continue
            if (files.isEmpty() && chessFiles.matches(raw)) {
                files = raw.trim().split(Regex("""\s+"""))
                continue
            }
            if (rows.size >= 8) continue
            val m = rowLine.find(raw) ?: continue
            val toks = chessToken.findAll(m.groupValues[2]).map {
                val paren = it.groupValues[1]
                if (paren.isNotEmpty()) paren to true else it.groupValues[2] to false
            }.toList()
            if (toks.size != 8) continue
            rows += toks
            ranks += m.groupValues[1].toInt()
            lastIdx = i
        }
        if (rows.size != 8 || files.size != 8) return null
        val cells = mutableListOf<String>()
        val last = mutableSetOf<Int>()
        rows.forEachIndexed { r, toks ->
            toks.forEachIndexed { c, (sym, hi) ->
                cells += if (sym == "·") "" else sym
                if (hi) last += r * 8 + c
            }
        }
        val (turnSide, turnName) = findTurn(block)
        val info = listOfNotNull(lastLineMatching(block, Regex("""^(上一步|Last move)[：:].*"""))?.value)
        return GameSnapshot(
            kind = GameKind.CHESS, state = state, header = block[0].trim(),
            board = Board(8, 8, cells, last, ranks, files),
            flipped = files.first() == "h",
            players = headerPlayers(block[0]), turnSide = turnSide, turnName = turnName, info = info,
            messages = messagesAfter(block, lastTurnIndex(block, lastIdx)),
        )
    }

    // ---------- go / gomoku / reversi ----------

    private val stoneToken = Regex("""\(([.#o])\)|!?([.#o])""")

    private fun stoneRows(
        block: List<String>,
        maxRows: Int,
    ): Triple<MutableList<String>, MutableSet<Int>, Pair<Int, Int>>? {
        val grid = mutableListOf<List<Pair<String, Boolean>>>()
        var lastIdx = 0
        for ((i, raw) in block.withIndex()) {
            if (i == 0 || grid.size >= maxRows) continue
            val m = rowLine.find(raw) ?: continue
            if (m.groupValues[1].toInt() != grid.size + 1) continue
            val toks = stoneToken.findAll(m.groupValues[2]).map {
                val paren = it.groupValues[1]
                if (paren.isNotEmpty()) paren to true
                else it.groupValues[2] to it.value.startsWith("!")
            }.toList()
            if (toks.size < 5 || (grid.isNotEmpty() && toks.size != grid[0].size)) continue
            grid += toks
            lastIdx = i
        }
        if (grid.isEmpty() || grid.size != grid[0].size) return null
        val size = grid.size
        val cells = mutableListOf<String>()
        val last = mutableSetOf<Int>()
        grid.forEachIndexed { r, toks ->
            toks.forEachIndexed { c, (s, hi) ->
                cells += when (s) {
                    "#" -> "B"
                    "o" -> "W"
                    else -> ""
                }
                if (hi) last += r * size + c
            }
        }
        return Triple(cells, last, size to lastIdx)
    }

    private fun parseStones(kind: GameKind, state: String, block: List<String>): GameSnapshot? {
        val (cells, last, meta) = stoneRows(block, 19) ?: return null
        val (size, lastIdx) = meta
        val (turnSide, turnName) = findTurn(block)
        val info = buildList {
            lastLineMatching(block, Regex("""^(贴目|Komi)[：:].*"""))?.let { add(it.value) }
            lastLineMatching(block, Regex("""^(上一步|Last move)[：:].*"""))?.let { add(it.value) }
        }
        return GameSnapshot(
            kind = kind, state = state, header = block[0].trim(),
            board = Board(size, size, cells, last),
            players = headerPlayers(block[0]), turnSide = turnSide, turnName = turnName, info = info,
            messages = messagesAfter(block, lastTurnIndex(block, lastIdx)),
        )
    }

    private fun parseReversi(state: String, block: List<String>): GameSnapshot? {
        val (cells, last, meta) = stoneRows(block, 8) ?: return null
        val (turnSide, turnName) = findTurn(block)
        val info = listOfNotNull(lastLineMatching(block, Regex("""^(Score|比分)[：:].*"""))?.value)
        return GameSnapshot(
            kind = GameKind.REVERSI, state = state, header = block[0].trim(),
            board = Board(meta.first, meta.first, cells, last),
            players = headerPlayers(block[0]), turnSide = turnSide, turnName = turnName, info = info,
            messages = messagesAfter(block, lastTurnIndex(block, meta.second)),
        )
    }

    // ---------- darkchess ----------

    private val darkToken = Regex("""!?(?:[+\-][^\s!+\-?.]|\?|\.)""")
    private val darkLetters = mapOf(
        'G' to '将', 'A' to '士', 'E' to '象', 'R' to '车',
        'H' to '马', 'C' to '炮', 'S' to '卒',
    )

    private fun parseDarkchess(state: String, block: List<String>): GameSnapshot? {
        val rows = collectRows(block, 8, 4, darkToken)
        if (rows.rows.size != 4) return null
        val cells = mutableListOf<String>()
        val last = mutableSetOf<Int>()
        rows.rows.forEachIndexed { r, toks ->
            toks.forEachIndexed { c, raw ->
                var tok = raw
                if (tok.startsWith("!")) {
                    last += r * 8 + c
                    tok = tok.substring(1)
                }
                cells += when {
                    tok == "?" -> "?"
                    tok == "." -> ""
                    else -> (if (tok[0] == '+') "r" else "b") + (darkLetters[tok[1]] ?: tok[1])
                }
            }
        }
        val players = headerPlayers(block[0]).toMutableMap()
        lastLineMatching(block, Regex("""(?:阵营|Sides)[：:]\s*P1\s*(\S+)\s+P2\s*(\S+)"""))?.let {
            players["p1side"] = if (it.groupValues[1].startsWith("红") || it.groupValues[1].startsWith("red")) "red"
            else if (it.groupValues[1].startsWith("黑") || it.groupValues[1].startsWith("black")) "black" else ""
            players["p2side"] = if (it.groupValues[2].startsWith("红") || it.groupValues[2].startsWith("red")) "red"
            else if (it.groupValues[2].startsWith("黑") || it.groupValues[2].startsWith("black")) "black" else ""
        }
        val (turnSide, turnName) = findTurn(block)
        val info = listOfNotNull(lastLineMatching(block, Regex("""^(上一步|Last move)[：:].*"""))?.value)
        return GameSnapshot(
            kind = GameKind.DARKCHESS, state = state, header = block[0].trim(),
            board = Board(4, 8, cells, last),
            players = players, turnSide = turnSide, turnName = turnName, info = info,
            messages = messagesAfter(block, lastTurnIndex(block, rows.lastIdx)),
        )
    }

    // ---------- doushou ----------

    private val dsToken = Regex("""!?[+\-][^\s·!]|!|黑陷|黑穴|红陷|红穴|河|·|bT|bD|rT|rD|RV""")
    private val dsLetters = mapOf(
        'R' to '鼠', 'C' to '猫', 'D' to '狗', 'W' to '狼',
        'P' to '豹', 'T' to '虎', 'L' to '狮', 'E' to '象',
    )
    private val dsColHeader = Regex("""^\s*([1-7])(?:\s+[1-7]){6}\s*$""")

    private fun parseDoushou(state: String, block: List<String>): GameSnapshot? {
        val rows = collectRows(block, 7, 9, dsToken)
        if (rows.rows.size != 9) return null
        val colLabels = block.firstOrNull { dsColHeader.matches(it) }?.trim()?.split(Regex("""\s+"""))
            ?: (1..7).map { it.toString() }
        val cells = mutableListOf<String>()
        val last = mutableSetOf<Int>()
        rows.rows.forEachIndexed { r, toks ->
            toks.forEachIndexed { c, raw ->
                var tok = raw
                if (tok.startsWith("!")) {
                    last += r * 7 + c
                    tok = tok.substring(1)
                }
                cells += when {
                    tok.length == 2 && (tok[0] == '+' || tok[0] == '-') ->
                        (if (tok[0] == '+') "r" else "b") + (dsLetters[tok[1]] ?: tok[1])
                    tok == "·" || tok == "." || tok == "*" -> ""
                    tok == "RV" -> "河"
                    tok == "rD" -> "红穴"
                    tok == "bD" -> "黑穴"
                    tok == "rT" -> "红陷"
                    tok == "bT" -> "黑陷"
                    tok in setOf("河", "红穴", "黑穴", "红陷", "黑陷") -> tok
                    else -> ""
                }
            }
        }
        val (turnSide, turnName) = findTurn(block)
        return GameSnapshot(
            kind = GameKind.DOUSHOU, state = state, header = block[0].trim(),
            board = Board(9, 7, cells, last, rows.labels, colLabels),
            flipped = rows.labels.firstOrNull() == 9,
            players = headerPlayers(block[0]), turnSide = turnSide, turnName = turnName,
            messages = messagesAfter(block, lastTurnIndex(block, rows.lastIdx)),
        )
    }

    // ---------- junqi ----------

    private val junqiToken = Regex("""!?(?:[+\-][A-Z]|\?|\.)""")

    private fun parseJunqi(state: String, block: List<String>): GameSnapshot? {
        val rows = collectRows(block, 5, 12, junqiToken)
        if (rows.rows.size != 12) return null
        val cells = mutableListOf<String>()
        val last = mutableSetOf<Int>()
        rows.rows.forEachIndexed { r, toks ->
            toks.forEachIndexed { c, raw ->
                var tok = raw
                if (tok.startsWith("!")) {
                    last += r * 5 + c
                    tok = tok.substring(1)
                }
                cells += when {
                    tok == "?" -> "?"
                    tok == "." -> ""
                    else -> (if (tok[0] == '+') "r" else "b") + tok[1]
                }
            }
        }
        val (turnSide, turnName) = findTurn(block)
        return GameSnapshot(
            kind = GameKind.JUNQI, state = state, header = block[0].trim(),
            board = Board(12, 5, cells, last),
            players = headerPlayers(block[0]), turnSide = turnSide, turnName = turnName,
            messages = messagesAfter(block, lastTurnIndex(block, rows.lastIdx)),
        )
    }

    // ---------- battleship ----------

    private val shipToken = Regex("""!?[SXo.?]""")

    private fun parseBattleship(state: String, block: List<String>): GameSnapshot? {
        val rows = collectRows(block, 10, 10, shipToken, tokenCount = 20)
        if (rows.rows.size != 10) return null
        val own = mutableListOf<String>()
        val enemy = mutableListOf<String>()
        val last = mutableSetOf<Int>()
        rows.rows.forEachIndexed { r, toks ->
            toks.forEachIndexed { c, raw ->
                if (c < 10) {
                    if (raw.startsWith("!")) last += r * 10 + c
                    own += raw.removePrefix("!")
                } else {
                    enemy += raw.removePrefix("!")
                }
            }
        }
        val (turnSide, turnName) = findTurn(block)
        return GameSnapshot(
            kind = GameKind.BATTLESHIP, state = state, header = block[0].trim(),
            board = Board(10, 10, own, last), board2 = Board(10, 10, enemy),
            players = headerPlayers(block[0]), turnSide = turnSide, turnName = turnName,
            messages = messagesAfter(block, lastTurnIndex(block, rows.lastIdx)),
        )
    }

    // ---------- cards ----------

    private val cardToken = Regex("""(黑桃|红桃|梅花|方块|♠|♥|♣|♦)\s*(10|[2-9JQKA])""")

    fun parseCards(text: String): List<String> =
        cardToken.findAll(text).map { m ->
            val suit = when (m.groupValues[1]) {
                "黑桃" -> "♠"
                "红桃" -> "♥"
                "梅花" -> "♣"
                "方块" -> "♦"
                else -> m.groupValues[1]
            }
            suit + m.groupValues[2]
        }.toList()

    private val pokerSeat = Regex("""^#(\d+)\s+(\S+?)[：:]\s*(?:积分|chips)=(-?\d+)\s*(.*)$""")
    private val kv = Regex("""^(底池|Pot|当前注|Current bet|阶段|Street|房主|Host)\s*[=：:]\s*(.+)$""")
    private val handLine = Regex("""^(?:你的手牌|Your hand)[：:]\s*(.*)$""")
    private val communityLine = Regex("""^(?:公共牌|Community cards)[：:]\s*(.*)$""")

    private fun parsePoker(kind: GameKind, state: String, block: List<String>): GameSnapshot {
        val seats = linkedMapOf<Int, Seat>()
        val fields = linkedMapOf<String, String>()
        var hand = emptyList<String>()
        var community = emptyList<String>()
        for (raw in block.drop(1)) {
            val t = raw.trim()
            pokerSeat.find(t)?.let { m ->
                val detail = m.groupValues[4]
                val idx = m.groupValues[1].toInt()
                seats[idx] = Seat(
                    index = idx, name = m.groupValues[2],
                    detail = "${m.groupValues[3]} · $detail".trim(),
                    active = "行动中" in detail || "acting" in detail.lowercase(),
                    alive = !("弃牌" in detail || "出局" in detail || "fold" in detail.lowercase()),
                )
                return@let
            }
            kv.find(t)?.let { m ->
                val key = when (m.groupValues[1]) {
                    "底池", "Pot" -> "pot"
                    "当前注", "Current bet" -> "bet"
                    "阶段", "Street" -> "stage"
                    else -> "host"
                }
                fields[key] = m.groupValues[2].trim()
            }
            handLine.find(t)?.let { hand = parseCards(it.groupValues[1]) }
            communityLine.find(t)?.let { community = parseCards(it.groupValues[1]) }
            if ("闷牌中" in t || "still concealed" in t) {
                fields["concealed"] = "1"
                hand = emptyList()
            }
        }
        val (_, turnName) = findTurn(block)
        val active = turnName ?: seats.values.firstOrNull { it.active }?.name
        return GameSnapshot(
            kind = kind, state = state, header = block[0].trim(),
            seats = seats.values.map { it.copy(active = it.name == active) },
            hand = hand, community = community, fields = fields, turnName = active,
            messages = block.drop(1).map { it.trim() }.filter { t ->
                t.isNotEmpty() && pokerSeat.find(t) == null && kv.find(t) == null &&
                    handLine.find(t) == null && communityLine.find(t) == null &&
                    !t.startsWith("行牌") && !t.startsWith("Actions") &&
                    !t.startsWith("完整对照") && !t.startsWith("Full reference") &&
                    "闷牌中" !in t && "still concealed" !in t
            }.takeLast(4),
        )
    }

    private val ntwSeat = Regex("""^-\s+(\S+?)[：:]\s*(?:牛头|bulls)=(\d+)[，,]\s*(?:手牌数|cards)=(\d+)""")
    private val ntwRow = Regex("""^(?:第(\d)行|Row (\d))[：:]\s*([\d\s]+?)\s*[（(](?:牛头|bulls)=(\d+)[）)]""")
    private val ntwRound = Regex("""^(?:回合|Round)[：:]\s*(\d+)""")

    private fun parseNiutou(state: String, block: List<String>, myName: String): GameSnapshot {
        val seats = linkedMapOf<String, Seat>()
        val rows = arrayOfNulls<Pair<List<Int>, Int>>(4)
        val fields = linkedMapOf<String, String>()
        var hand = emptyList<String>()
        var mustRow = false
        for (raw in block.drop(1)) {
            val t = raw.trim()
            ntwSeat.find(t)?.let { m ->
                seats[m.groupValues[1]] = Seat(
                    seats.size + 1, m.groupValues[1],
                    "牛头 ${m.groupValues[2]} · 手牌 ${m.groupValues[3]}",
                )
            }
            ntwRow.find(t)?.let { m ->
                val i = (m.groupValues[1].ifEmpty { m.groupValues[2] }).toInt() - 1
                if (i in 0..3) {
                    rows[i] = m.groupValues[3].trim().split(Regex("""\s+"""))
                        .mapNotNull { it.toIntOrNull() } to m.groupValues[4].toInt()
                }
            }
            ntwRound.find(t)?.let { fields["round"] = it.groupValues[1] }
            handLine.find(t)?.let {
                hand = it.groupValues[1].trim().split(Regex("""\s+""")).filter { s -> s.toIntOrNull() != null }
            }
            if ("必须选择一行" in t || "must choose a row" in t.lowercase()) mustRow = true
            val need = Regex("""^(\S+) (?:需要选行|must pick a row)""").find(t)
            if (need != null && need.groupValues[1] == myName) mustRow = true
        }
        if (mustRow) fields["mustRow"] = "1"
        return GameSnapshot(
            kind = GameKind.NIUTOU, state = state, header = block[0].trim(),
            seats = seats.values.toList(), hand = hand, rows = rows.filterNotNull(), fields = fields,
            messages = block.drop(1).map { it.trim() }.filter {
                it.isNotEmpty() && ntwSeat.find(it) == null && ntwRow.find(it) == null &&
                    ntwRound.find(it) == null && handLine.find(it) == null &&
                    !it.startsWith("房主") && !it.startsWith("Host")
            }.takeLast(4),
        )
    }

    private val mjSeat = Regex("""^(东|南|西|北)家(?:（房主）)?[：:]\s*(\S+)(.*)$""")
    private val mjSeatEn = Regex("""^(East|South|West|North)(?: \(host\))?[：:]\s*(\S+)(.*)$""")
    private val mjWall = Regex("""^(?:牌墙剩余|Wall left)[：:]\s*(\d+)""")
    private val mjDiscards = Regex("""^(?:最近弃牌|Recent discards)[：:]\s*(.*)$""")
    private val mjMelds = Regex("""^(?:你的副露|Your melds)[：:]\s*(.*)$""")
    private val mjTile = Regex("""\b([mps][1-9]|z[1-7])\b""")

    private fun parseMahjong(state: String, block: List<String>): GameSnapshot {
        val seats = mutableListOf<Seat>()
        val fields = linkedMapOf<String, String>()
        var hand = emptyList<String>()
        var discards = emptyList<String>()
        val hints = mutableListOf<String>()
        for (raw in block.drop(1)) {
            val t = raw.trim()
            val sm = mjSeat.find(t) ?: mjSeatEn.find(t)
            if (sm != null) {
                val rest = sm.groupValues[3]
                val wind = when (sm.groupValues[1]) {
                    "East" -> "东"
                    "South" -> "南"
                    "West" -> "西"
                    "North" -> "北"
                    else -> sm.groupValues[1]
                }
                seats += Seat(
                    index = seats.size + 1, name = sm.groupValues[2],
                    detail = wind + "家" + if ("[AI]" in rest) " · AI" else "",
                    active = "<-" in rest,
                )
                continue
            }
            mjWall.find(t)?.let { fields["wall"] = it.groupValues[1] }
            mjDiscards.find(t)?.let {
                discards = mjTile.findAll(it.groupValues[1]).map { x -> x.value }.toList()
            }
            mjMelds.find(t)?.let { fields["melds"] = it.groupValues[1] }
            handLine.find(t)?.let {
                hand = mjTile.findAll(it.groupValues[1]).map { x -> x.value }.toList()
            }
            if (t.startsWith("你可用") || t.startsWith("You can")) fields["canAct"] = "turn"
            if (t.startsWith("当前可用") || "chi <" in t) fields["canAct"] = "claim"
            if (t.startsWith("你已") || t.startsWith("当前为响应") || t.startsWith("当前轮到")) hints += t
        }
        return GameSnapshot(
            kind = GameKind.MAHJONG, state = state, header = block[0].trim(),
            seats = seats, hand = hand, community = discards, fields = fields, hints = hints.takeLast(1),
            turnName = seats.firstOrNull { it.active }?.name,
            messages = block.drop(1).map { it.trim() }.filter {
                it.isNotEmpty() && mjSeat.find(it) == null && mjSeatEn.find(it) == null &&
                    mjWall.find(it) == null && mjDiscards.find(it) == null &&
                    handLine.find(it) == null && mjMelds.find(it) == null &&
                    !it.startsWith("你可用") && !it.startsWith("You can") && !it.startsWith("当前可用")
            }.takeLast(4),
        )
    }

    // ---------- sanguosha ----------

    private val sgsSeat = Regex("""^#(\d+)\s+(\S+)\s+(.*?)体力\s*(-?\d+)/(\d+)\s*手牌=(.*)$""")
    private val sgsCardItem = Regex("""(\d+)\.([^、\s]+)""")
    private val sgsHandHead = Regex("""^──\s*你的手牌""")

    fun sgsHandCards(text: String): List<String> =
        sgsCardItem.findAll(text).map { it.groupValues[2] }.toList()

    private fun parseSanguo(state: String, block: List<String>): GameSnapshot {
        val seats = linkedMapOf<Int, Seat>()
        var hand = emptyList<String>()
        val fields = linkedMapOf<String, String>()
        val hints = mutableListOf<String>()
        var turn: String? = null
        var expectHand = false
        var inHint = false
        for ((i, raw) in block.withIndex()) {
            val t = raw.trim()
            if (i == 0) {
                Regex("""牌堆(\d+)""").find(t)?.let { fields["deck"] = it.groupValues[1] }
                continue
            }
            if (expectHand) {
                expectHand = false
                if (sgsCardItem.containsMatchIn(t)) {
                    hand = sgsHandCards(t)
                    continue
                }
            }
            if (sgsHandHead.containsMatchIn(t)) {
                expectHand = true
                if ("（0张）" in t) hand = emptyList()
                continue
            }
            sgsSeat.find(t)?.let { m ->
                val idx = m.groupValues[1].toInt()
                val name = m.groupValues[2].removePrefix("▸")
                val mid = m.groupValues[3].trim()
                val handTxt = m.groupValues[6].trim()
                val mine = sgsCardItem.containsMatchIn(handTxt)
                if (mine) hand = sgsHandCards(handTxt)
                val hp = m.groupValues[4].toInt()
                seats[idx] = Seat(
                    index = idx, name = name,
                    detail = mid.replace(Regex("""身份=\s*"""), "").replace(Regex("""\s+"""), " ").trim() +
                        if (!mine) "  手牌 ${handTxt.removeSuffix("张")}" else "",
                    alive = hp > 0 && "阵亡" !in t,
                    hp = hp, maxHp = m.groupValues[5].toInt(),
                )
                return@let
            }
            Regex("""(?:你的身份|Your role)[：:]\s*(\S+)""").find(t)?.let { fields["role"] = it.groupValues[1] }
            Regex("""当前回合[：:]\s*#\d+\s*(\S+)""").find(t)?.let { turn = it.groupValues[1] }
            Regex("""轮到\s*#\d+\s*(\S+)\s*的回合""").find(t)?.let { turn = it.groupValues[1] }
            if (t.startsWith("▸")) {
                inHint = true
                hints.clear()
                hints += t
                continue
            }
            if (inHint && (t.startsWith("牌顶") || raw.startsWith("     "))) {
                hints += t
                continue
            }
            inHint = false
            if (t.startsWith("需") || "响应" in t || "请出" in t || "是否" in t) hints += t
        }
        return GameSnapshot(
            kind = GameKind.SANGUO, state = state, header = block[0].trim(),
            seats = seats.values.map { it.copy(active = it.name == turn) },
            hand = hand, fields = fields, turnName = turn, hints = hints.takeLast(8),
            messages = block.drop(1).map { it.trim() }.filter {
                it.isNotEmpty() && sgsSeat.find(it) == null && !it.startsWith("座次") &&
                    !it.startsWith("#") && !it.startsWith("距离") && !it.startsWith("当前回合") &&
                    !it.startsWith("你的") && !it.startsWith("──") && !it.startsWith("指令详情") &&
                    !it.startsWith("武器") && !it.startsWith("▸") && !it.startsWith("牌顶")
            }.takeLast(4),
        )
    }

    // ---------- werewolf ----------

    private fun parseWerewolf(state0: String, block: List<String>): GameSnapshot {
        var state = state0
        var round = ""
        var alive = emptyList<String>()
        val joined = linkedSetOf<String>()
        var votes = ""
        for (raw in block) {
            val t = raw.trim()
            Regex("""^werewolf state:\s*(\S+)""").find(t)?.let { state = it.groupValues[1] }
            Regex("""^round:\s*(\d+)""").find(t)?.let { round = it.groupValues[1] }
            Regex("""^Alive:\s*(.*)$""").find(t)?.let {
                alive = it.groupValues[1].split(",").map { s -> s.trim() }.filter { s -> s.isNotEmpty() }
            }
            Regex("""^(\S+) joined werewolf""").find(t)?.let { joined += it.groupValues[1] }
            Regex("""^(Day|Night) (\d+)(?: begins)?\.""").find(t)?.let {
                state = it.groupValues[1].lowercase()
                round = it.groupValues[2]
            }
            Regex("""^Werewolf started\. Night (\d+)""").find(t)?.let {
                state = "night"
                round = it.groupValues[1]
            }
            Regex("""^votes:\s*(\S+)""").find(t)?.let { votes = it.groupValues[1] }
            Regex("""\((\d+/\d+)\)$""").find(t)?.takeIf { " voted " in t }?.let { votes = it.groupValues[1] }
            Regex("""^Voted out:\s*(\S+)""").find(t)?.let { out -> alive = alive - out.groupValues[1] }
            Regex("""^Night deaths:\s*(.*)$""").find(t)?.let { d ->
                val dead = d.groupValues[1].split(",").map { it.trim() }.toSet()
                alive = alive.filter { it !in dead }
            }
            if (t == "Villagers win." || t == "Wolves win.") state = "ended"
        }
        if (alive.isEmpty() && joined.isNotEmpty()) alive = joined.toList()
        return GameSnapshot(
            kind = GameKind.WEREWOLF, state = state, header = block[0].trim(),
            seats = alive.mapIndexed { i, n -> Seat(i + 1, n, "") },
            fields = buildMap {
                if (round.isNotEmpty()) put("round", round)
                if (votes.isNotEmpty()) put("votes", votes)
            },
            messages = block.drop(1).map { it.trim() }.filter {
                it.isNotEmpty() && !it.startsWith("werewolf state") &&
                    !it.startsWith("round:") && !it.startsWith("Alive:")
            }.takeLast(4),
        )
    }
}
