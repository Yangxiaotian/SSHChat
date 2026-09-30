package chat.ssh.sshchat.games

import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.util.AttributeSet
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.EditText
import android.widget.HorizontalScrollView
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat

/**
 * 折叠式游戏 UI：解析 [*] 文本 → 棋盘/手牌 → 点击发 /game move。
 */
class GamePanel @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : LinearLayout(context, attrs) {

    var onSend: ((String) -> Unit)? = null
    var onMaximizeChanged: ((Boolean) -> Unit)? = null
    var myName: String = ""

    private val buffer = ArrayDeque<String>(256)
    private var snap: GameSnapshot? = null
    private var visible = false
    private var maximized = false

    private var fromCell: Pair<Int, Int>? = null
    private var pendingStone: Pair<Int, Int>? = null
    private var selectedHand: String? = null
    private var selectedTarget: String? = null
    private var battleshipOrientH = true
    private var battleshipShipIdx = 0
    private var junqiPieceIdx = 0

    private val titleBar: TextView
    private val statusLine: TextView
    private val body: LinearLayout
    private val actions: LinearLayout
    private val boardView: BoardView
    private val board2View: BoardView
    private val board2Label: TextView
    private val handRow: LinearLayout
    private val seatArea: LinearLayout
    private val msgLine: TextView
    private val collapseBtn: TextView
    private val maximizeBtn: TextView
    private var boardLayoutParams: LayoutParams
    private var bodyLayoutParams: LayoutParams

    private val padL = dp(8)
    private val padT = dp(6)
    private val padR = dp(8)
    private val padB = dp(6)
    private var insetTop = 0
    private var insetBottom = 0

    private val battleshipShips = listOf(
        "carrier" to 5, "battleship" to 4, "cruiser" to 3, "submarine" to 3, "destroyer" to 2,
    )
    private val junqiPieces = listOf(
        "flag", "commander", "army", "division", "division",
        "brigade", "brigade", "regiment", "regiment",
        "battalion", "battalion", "company", "company", "company",
        "platoon", "platoon", "platoon", "engineer", "engineer", "engineer",
        "mine", "mine", "mine", "bomb", "bomb",
    )

    init {
        orientation = VERTICAL
        visibility = GONE
        setBackgroundColor(Color.parseColor("#FAFAFA"))
        applyChromePadding()

        val header = LinearLayout(context).apply {
            orientation = HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            minimumHeight = dp(44)
        }
        titleBar = TextView(context).apply {
            text = "棋盘"
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
            typeface = Typeface.DEFAULT_BOLD
            setTextColor(Color.parseColor("#1B5E20"))
            layoutParams = LayoutParams(0, LayoutParams.WRAP_CONTENT, 1f)
        }
        maximizeBtn = TextView(context).apply {
            text = "最大化"
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
            typeface = Typeface.DEFAULT_BOLD
            setTextColor(Color.parseColor("#1B5E20"))
            setPadding(dp(12), dp(10), dp(12), dp(10))
            minHeight = dp(44)
            // 全屏时顶部易被刘海/手势吃掉点击；加大命中区
            setOnClickListener { setMaximized(!maximized) }
        }
        collapseBtn = TextView(context).apply {
            text = "收起"
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
            setTextColor(Color.parseColor("#1B5E20"))
            setPadding(dp(8), dp(4), dp(8), dp(4))
            setOnClickListener { setPanelVisible(false) }
        }
        header.addView(titleBar)
        header.addView(maximizeBtn)
        header.addView(collapseBtn)
        addView(header)

        // 全屏沉浸时系统栏隐藏，需用 IgnoringVisibility 才能给「还原」留出刘海区。
        ViewCompat.setOnApplyWindowInsetsListener(this) { _, insets ->
            if (maximized) {
                val bars = insets.getInsetsIgnoringVisibility(
                    WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout(),
                )
                insetTop = bars.top
                insetBottom = bars.bottom
            } else {
                insetTop = 0
                insetBottom = 0
            }
            applyChromePadding()
            insets
        }

        statusLine = TextView(context).apply {
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
            setTextColor(Color.parseColor("#616161"))
            setPadding(0, dp(2), 0, dp(4))
        }
        addView(statusLine)

        body = LinearLayout(context).apply { orientation = VERTICAL }
        boardView = BoardView(context)
        boardLayoutParams = LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT)
        boardView.layoutParams = boardLayoutParams
        board2Label = TextView(context).apply {
            text = "对方海域"
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 11f)
            setTextColor(Color.parseColor("#757575"))
            visibility = GONE
        }
        board2View = BoardView(context).apply { visibility = GONE }
        seatArea = LinearLayout(context).apply { orientation = VERTICAL }
        handRow = LinearLayout(context).apply {
            orientation = HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        msgLine = TextView(context).apply {
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 11f)
            setTextColor(Color.parseColor("#757575"))
            setPadding(0, dp(2), 0, dp(2))
        }
        actions = LinearLayout(context).apply {
            orientation = HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }

        body.addView(seatArea)
        body.addView(boardView)
        body.addView(board2Label)
        body.addView(board2View)
        body.addView(HorizontalScrollView(context).apply {
            isHorizontalScrollBarEnabled = false
            addView(handRow)
        })
        body.addView(msgLine)
        body.addView(HorizontalScrollView(context).apply {
            isHorizontalScrollBarEnabled = false
            addView(actions)
            setPadding(0, dp(4), 0, 0)
        })

        bodyLayoutParams = LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT)
        body.layoutParams = bodyLayoutParams
        addView(body)

        boardView.onCellTap = { r, c -> onBoardTap(r, c, primary = true) }
        board2View.onCellTap = { r, c -> onBoardTap(r, c, primary = false) }
    }

    fun setPanelVisible(show: Boolean) {
        visible = show
        visibility = if (show) VISIBLE else GONE
        if (!show && maximized) {
            setMaximized(false)
        }
        if (show) {
            send("/game show")
            refresh()
        }
    }

    fun isPanelVisible(): Boolean = visible
    fun isMaximized(): Boolean = maximized

    fun setMaximized(max: Boolean) {
        if (maximized == max) return
        maximized = max
        maximizeBtn.text = if (max) "还原" else "最大化"
        collapseBtn.visibility = if (max) GONE else VISIBLE
        // 全屏时尽量吃满可用高度；内嵌时仍限制比例以免挤爆聊天。
        boardView.maxHeightFraction = if (max) 1.0f else 0.48f
        board2View.maxHeightFraction = if (max) 0.45f else 0.48f
        if (max) {
            bodyLayoutParams.height = 0
            bodyLayoutParams.weight = 1f
            boardLayoutParams.height = 0
            boardLayoutParams.weight = 1f
            setBackgroundColor(Color.WHITE)
            elevation = dp(8).toFloat()
        } else {
            bodyLayoutParams.height = LayoutParams.WRAP_CONTENT
            bodyLayoutParams.weight = 0f
            boardLayoutParams.height = LayoutParams.WRAP_CONTENT
            boardLayoutParams.weight = 0f
            insetTop = 0
            insetBottom = 0
            setBackgroundColor(Color.parseColor("#FAFAFA"))
            elevation = dp(2).toFloat()
        }
        applyChromePadding()
        body.layoutParams = bodyLayoutParams
        boardView.layoutParams = boardLayoutParams
        requestLayout()
        // 先通知宿主换父布局，再申请 insets（全屏时父级已是 root）。
        onMaximizeChanged?.invoke(max)
        if (max) {
            ViewCompat.requestApplyInsets(this)
        }
    }

    private fun applyChromePadding() {
        setPadding(padL, padT + insetTop, padR, padB + insetBottom)
    }

    fun clear() {
        buffer.clear()
        snap = null
        fromCell = null
        pendingStone = null
        selectedHand = null
        selectedTarget = null
        refresh()
    }

    /** 喂入一条已去掉 [*] 前缀的游戏行。 */
    fun feedLine(text: String) {
        val t = text.trimEnd()
        if (t.isEmpty()) return
        if (buffer.size >= 240) {
            repeat(40) { if (buffer.isNotEmpty()) buffer.removeFirst() }
        }
        buffer.addLast(t)
        val parsed = GameParser.parseLatest(buffer.toList(), myName) ?: return
        snap = parsed
        if (visible) refresh()
        else if (GameParser.detectHeader(t) != null) {
            // 有新对局时提示标题，但不自动弹出
            titleBar.text = "棋盘 · ${parsed.kind.title}"
        }
    }

    private fun refresh() {
        val s = snap
        if (s == null) {
            titleBar.text = "棋盘"
            statusLine.text = "当前房间没有可识别的对局。可用 /game new <名称> 开局。"
            boardView.board = null
            board2View.visibility = GONE
            board2Label.visibility = GONE
            handRow.removeAllViews()
            seatArea.removeAllViews()
            actions.removeAllViews()
            msgLine.text = ""
            addLobbyActions()
            return
        }
        titleBar.text = "${s.kind.title} · ${s.state}"
        statusLine.text = buildStatus(s)
        msgLine.text = s.messages.joinToString(" · ").ifEmpty {
            s.info.joinToString(" · ").ifEmpty { s.hints.joinToString(" · ") }
        }
        fromCell = null
        pendingStone = null
        boardView.selected = null
        boardView.highlights = emptySet()

        when (s.kind) {
            GameKind.XIANGQI, GameKind.DOUSHOU, GameKind.DARKCHESS, GameKind.JUNQI,
            GameKind.CHESS, GameKind.GO, GameKind.GOMOKU, GameKind.REVERSI, GameKind.BATTLESHIP,
            -> renderBoardGame(s)
            GameKind.HOLDEM, GameKind.ZJH -> renderPoker(s)
            GameKind.NIUTOU -> renderNiutou(s)
            GameKind.MAHJONG -> renderMahjong(s)
            GameKind.SANGUO -> renderSanguo(s)
            GameKind.WEREWOLF -> renderWerewolf(s)
        }
    }

    private fun buildStatus(s: GameSnapshot): String = buildString {
        val seats = s.players.entries.joinToString("  ") { "${sideZh(it.key)}${it.value}" }
        if (seats.isNotEmpty()) append(seats)
        if (s.turnName != null) {
            if (isNotEmpty()) append("  ·  ")
            append("轮到 ${s.turnName}")
        } else if (s.turnSide != null) {
            if (isNotEmpty()) append("  ·  ")
            append("轮到 ${sideZh(s.turnSide)}")
        }
        s.fields["pot"]?.let { if (isNotEmpty()) append("  ·  "); append("底池 $it") }
        s.fields["stage"]?.let { if (isNotEmpty()) append("  ·  "); append(it) }
        s.fields["role"]?.let { if (isNotEmpty()) append("  ·  "); append("身份 $it") }
        s.fields["round"]?.let { if (isNotEmpty()) append("  ·  "); append("第 $it 轮") }
    }

    private fun sideZh(side: String): String = when (side) {
        "red" -> "红:"
        "black" -> "黑:"
        "white" -> "白:"
        "blue" -> "蓝:"
        "p1" -> "P1:"
        "p2" -> "P2:"
        else -> "$side:"
    }

    // ---------- board games ----------

    private fun renderBoardGame(s: GameSnapshot) {
        seatArea.removeAllViews()
        handRow.removeAllViews()
        actions.removeAllViews()
        boardView.visibility = VISIBLE
        boardView.style = when (s.kind) {
            GameKind.XIANGQI -> BoardView.Style.XIANGQI
            GameKind.CHESS -> BoardView.Style.CHESS
            GameKind.GO, GameKind.GOMOKU, GameKind.REVERSI -> BoardView.Style.STONES
            GameKind.JUNQI -> BoardView.Style.JUNQI
            GameKind.BATTLESHIP -> BoardView.Style.SHIP
            GameKind.DOUSHOU -> BoardView.Style.DOUSHOU
            else -> BoardView.Style.GRID
        }
        boardView.board = s.board
        if (s.kind == GameKind.BATTLESHIP && s.board2 != null) {
            board2Label.visibility = VISIBLE
            board2Label.text = if (s.isSetup) "对方海域（布置阶段不可开火）" else "对方海域（点格子开火）"
            board2View.visibility = VISIBLE
            board2View.style = BoardView.Style.SHIP
            board2View.board = s.board2
        } else {
            board2Label.visibility = GONE
            board2View.visibility = GONE
            board2View.board = null
        }
        if (s.kind == GameKind.REVERSI && s.board != null) {
            boardView.highlights = legalReversi(s.board)
        }
        addCommonActions(s)
        when {
            s.isWaiting || s.state == "waiting" -> {
                actions.addView(GameUi.btn(context, "加入") { send("/game join") })
            }
            s.isSetup && s.kind == GameKind.BATTLESHIP -> {
                actions.addView(GameUi.btn(context, if (battleshipOrientH) "方向:横" else "方向:竖") {
                    battleshipOrientH = !battleshipOrientH
                    refresh()
                })
                actions.addView(GameUi.btn(context, "一键布置") { autoPlaceBattleship() })
                actions.addView(GameUi.btn(context, "准备 ready") { sendMove("ready") })
                statusLine.text = (statusLine.text?.toString().orEmpty()) +
                    "  ·  点己方海域布置 ${battleshipShips.getOrNull(battleshipShipIdx)?.first ?: "完成"}"
            }
            s.isSetup && s.kind == GameKind.JUNQI -> {
                actions.addView(GameUi.btn(context, "一键布阵") { autoSetupJunqi() })
                actions.addView(GameUi.btn(context, "准备 ready") { sendMove("ready") })
            }
        }
        requestLayout()
    }

    private fun onBoardTap(r: Int, c: Int, primary: Boolean) {
        val s = snap ?: return
        when (s.kind) {
            GameKind.GO, GameKind.GOMOKU, GameKind.REVERSI -> tapStone(s, r, c)
            GameKind.CHESS -> tapChess(s, r, c)
            GameKind.XIANGQI, GameKind.DOUSHOU, GameKind.JUNQI, GameKind.DARKCHESS -> tapMovePiece(s, r, c)
            GameKind.BATTLESHIP -> tapBattleship(s, r, c, primary)
            else -> {}
        }
    }

    private fun tapStone(s: GameSnapshot, r: Int, c: Int) {
        val b = s.board ?: return
        if (b.at(r, c).isNotEmpty() && s.kind != GameKind.REVERSI) {
            toast("该点已有棋子")
            return
        }
        val srv = displayToServer(s, r, c)
        pendingStone = null
        boardView.selected = null
        sendMove("${srv.first} ${srv.second}")
    }

    private fun tapChess(s: GameSnapshot, r: Int, c: Int) {
        val b = s.board ?: return
        val files = b.colLabels.ifEmpty { listOf("a", "b", "c", "d", "e", "f", "g", "h") }
        val ranks = b.rowLabels.ifEmpty { (8 downTo 1).toList() }
        if (r !in ranks.indices || c !in files.indices) return
        val sq = "${files[c]}${ranks[r]}"
        val from = fromCell
        if (from == null) {
            if (b.at(r, c).isEmpty()) return
            fromCell = r to c
            boardView.selected = r to c
        } else {
            val fr = from.first
            val fc = from.second
            val fromSq = "${files[fc]}${ranks[fr]}"
            fromCell = null
            boardView.selected = null
            if (fromSq != sq) sendMove("$fromSq$sq")
        }
    }

    private fun tapMovePiece(s: GameSnapshot, r: Int, c: Int) {
        val b = s.board ?: return
        val srv = displayToServer(s, r, c)
        if (s.kind == GameKind.DARKCHESS) {
            val tok = b.at(r, c)
            if (fromCell == null) {
                if (tok == "?") {
                    sendMove("flip ${srv.first} ${srv.second}")
                    return
                }
                if (tok.isEmpty()) return
                fromCell = r to c
                boardView.selected = r to c
                return
            }
            val fr = fromCell!!.first
            val fc = fromCell!!.second
            val fromSrv = displayToServer(s, fr, fc)
            fromCell = null
            boardView.selected = null
            if (fr == r && fc == c) return
            sendMove("move ${fromSrv.first} ${fromSrv.second} ${srv.first} ${srv.second}")
            return
        }
        if (s.isSetup && s.kind == GameKind.JUNQI) {
            val piece = junqiPieces.getOrNull(junqiPieceIdx) ?: return
            sendMove("setup $piece ${srv.first} ${srv.second}")
            junqiPieceIdx = (junqiPieceIdx + 1).coerceAtMost(junqiPieces.lastIndex)
            return
        }
        if (fromCell == null) {
            if (b.at(r, c).isEmpty() || b.at(r, c) == "?") return
            fromCell = r to c
            boardView.selected = r to c
        } else {
            val fr = fromCell!!.first
            val fc = fromCell!!.second
            val fromSrv = displayToServer(s, fr, fc)
            fromCell = null
            boardView.selected = null
            if (fr == r && fc == c) return
            when (s.kind) {
                GameKind.XIANGQI -> sendMove("coord ${fromSrv.first} ${fromSrv.second} ${srv.first} ${srv.second}")
                GameKind.JUNQI -> sendMove("move ${fromSrv.first} ${fromSrv.second} ${srv.first} ${srv.second}")
                else -> sendMove("${fromSrv.first} ${fromSrv.second} ${srv.first} ${srv.second}")
            }
        }
    }

    private fun tapBattleship(s: GameSnapshot, r: Int, c: Int, primary: Boolean) {
        val row = r + 1
        val col = c + 1
        if (s.isSetup) {
            if (!primary) {
                toast("布置阶段请点己方海域")
                return
            }
            val ship = battleshipShips.getOrNull(battleshipShipIdx) ?: run {
                toast("舰船已全部布置，点准备")
                return
            }
            val ori = if (battleshipOrientH) "h" else "v"
            sendMove("place ${ship.first} $row $col $ori")
            battleshipShipIdx = (battleshipShipIdx + 1).coerceAtMost(battleshipShips.size)
            return
        }
        if (s.isPlaying) {
            if (primary) {
                toast("开火请点右侧对方海域")
                return
            }
            sendMove("fire $row $col")
        }
    }

    /** 显示坐标 → 服务端 1-based（翻转棋盘用 rowLabels/colLabels）。 */
    private fun displayToServer(s: GameSnapshot, r: Int, c: Int): Pair<Int, Int> {
        val b = s.board ?: return (r + 1) to (c + 1)
        val row = if (b.rowLabels.isNotEmpty() && r < b.rowLabels.size) b.rowLabels[r] else r + 1
        val col = if (b.colLabels.isNotEmpty() && c < b.colLabels.size) {
            b.colLabels[c].toIntOrNull() ?: (c + 1)
        } else {
            c + 1
        }
        // 象棋没有 rowLabels；翻转时显示行 0 = 服务端行 10
        if (s.kind == GameKind.XIANGQI) {
            val rr = if (s.flipped) b.rows - r else r + 1
            val cc = if (s.flipped) b.cols - c else c + 1
            return rr to cc
        }
        if (s.kind == GameKind.DOUSHOU && b.rowLabels.isNotEmpty()) {
            return row to col
        }
        return row to col
    }

    private fun legalReversi(b: Board): Set<Pair<Int, Int>> {
        val out = mutableSetOf<Pair<Int, Int>>()
        // 简化：空点都标出来，合法着由服务端校验
        for (r in 0 until b.rows) for (c in 0 until b.cols) {
            if (b.at(r, c).isEmpty()) out += r to c
        }
        return out
    }

    private fun autoPlaceBattleship() {
        // 与测试 place_standard 一致
        listOf(
            "place carrier 1 1 h",
            "place battleship 3 1 h",
            "place cruiser 5 1 h",
            "place submarine 7 1 h",
            "place destroyer 9 1 h",
        ).forEach { sendMove(it) }
        battleshipShipIdx = battleshipShips.size
        toast("已发送标准布置，点准备")
    }

    private fun autoSetupJunqi() {
        // 简化：按测试 place_side 思路，红方 1-5 行填满（具体合法性由服务端校验）
        toast("请用一键布阵前先确认己方颜色；发送标准布阵指令…")
        // 让用户先 /game show 看清，再逐个 setup 太慢；发一批固定序列
        val pieces = listOf(
            "commander", "flag", "army", "division", "division",
            "bomb", "bomb", "brigade", "brigade", "regiment",
            "regiment", "battalion", "battalion", "company", "company",
            "mine", "mine", "mine", "company", "platoon",
            "platoon", "platoon", "engineer", "engineer", "engineer",
        )
        var i = 0
        for (row in 1..5) {
            for (col in 1..5) {
                if (i >= pieces.size) break
                sendMove("setup ${pieces[i]} $row $col")
                i++
            }
        }
    }

    // ---------- poker ----------

    private fun renderPoker(s: GameSnapshot) {
        boardView.visibility = GONE
        board2View.visibility = GONE
        board2Label.visibility = GONE
        seatArea.removeAllViews()
        seatArea.addView(GameUi.seatBar(context, s.seats))
        handRow.removeAllViews()
        if (s.community.isNotEmpty()) {
            handRow.addView(GameUi.label(context, "公牌 ", true))
            s.community.forEach { handRow.addView(GameUi.pokerCard(context, it)) }
            handRow.addView(TextView(context).apply { text = "  " })
        }
        handRow.addView(GameUi.label(context, "手牌 ", true))
        if (s.fields["concealed"] == "1") {
            // 炸金花闷牌：显示三张牌背
            repeat(3) { handRow.addView(GameUi.pokerBack(context)) }
        } else if (s.hand.isNotEmpty()) {
            s.hand.forEach { handRow.addView(GameUi.pokerCard(context, it)) }
        }
        actions.removeAllViews()
        addCommonActions(s)
        if (s.isWaiting) {
            actions.addView(GameUi.btn(context, "开始") { sendMove("start") })
            actions.addView(GameUi.btn(context, "加入") { send("/game join") })
        }
        if (s.isPlaying || s.isWaiting.not()) {
            // 看牌仅炸金花需要（闷牌→明牌）；德州发牌即可见底牌
            if (s.kind == GameKind.ZJH) {
                actions.addView(GameUi.btn(context, "看牌") { sendMove("look") })
                actions.addView(GameUi.btn(context, "跟注") { sendMove("follow") })
                actions.addView(GameUi.btn(context, "加注") { promptAmount("raise") })
                actions.addView(GameUi.btn(context, "比牌") { promptCompare() })
            } else {
                actions.addView(GameUi.btn(context, "过牌") { sendMove("check") })
                actions.addView(GameUi.btn(context, "跟注") { sendMove("call") })
                actions.addView(GameUi.btn(context, "加注") { promptAmount("raise") })
                actions.addView(GameUi.btn(context, "全下") { sendMove("allin") })
            }
            actions.addView(GameUi.btn(context, "弃牌") { sendMove("fold") })
        }
    }

    private fun promptAmount(verb: String) {
        val input = EditText(context).apply {
            hint = "金额"
            inputType = android.text.InputType.TYPE_CLASS_NUMBER
            setPadding(dp(16), dp(12), dp(16), dp(12))
        }
        android.app.AlertDialog.Builder(context)
            .setTitle(if (verb == "raise") "加注" else verb)
            .setView(input)
            .setPositiveButton("确定") { _, _ ->
                val n = input.text?.toString()?.trim().orEmpty()
                if (n.isNotEmpty()) sendMove("$verb $n")
            }
            .setNegativeButton("取消", null)
            .show()
    }

    private fun promptCompare() {
        val seats = snap?.seats?.filter { it.name != myName && it.alive } ?: emptyList()
        if (seats.isEmpty()) {
            toast("没有可比牌的对手")
            return
        }
        val names = seats.map { it.name }.toTypedArray()
        android.app.AlertDialog.Builder(context)
            .setTitle("比牌对象")
            .setItems(names) { _, which -> sendMove("compare ${names[which]}") }
            .setNegativeButton("取消", null)
            .show()
    }

    // ---------- niutou ----------

    private fun renderNiutou(s: GameSnapshot) {
        boardView.visibility = GONE
        board2View.visibility = GONE
        board2Label.visibility = GONE
        seatArea.removeAllViews()
        seatArea.addView(GameUi.seatBar(context, s.seats))
        // 四行
        for ((i, row) in s.rows.withIndex()) {
            val line = GameUi.row(context)
            line.addView(GameUi.label(context, "行${i + 1} ", true))
            row.first.forEach { n -> line.addView(GameUi.ntwCard(context, n)) }
            line.addView(GameUi.label(context, " 🐂${row.second}", true))
            seatArea.addView(line)
        }
        handRow.removeAllViews()
        val mustRow = s.fields["mustRow"] == "1"
        if (mustRow) {
            handRow.addView(GameUi.label(context, "必须选行："))
            for (i in 1..4) {
                handRow.addView(GameUi.btn(context, "行$i") { sendMove("row $i") })
            }
        } else {
            s.hand.forEach { h ->
                val n = h.toIntOrNull() ?: return@forEach
                handRow.addView(GameUi.ntwCard(context, n, selected = selectedHand == h) {
                    selectedHand = h
                    sendMove("pick $h")
                })
            }
        }
        actions.removeAllViews()
        addCommonActions(s)
        if (s.isWaiting) {
            actions.addView(GameUi.btn(context, "开始") { sendMove("start") })
            actions.addView(GameUi.btn(context, "加入") { send("/game join") })
        }
    }

    // ---------- mahjong ----------

    private fun renderMahjong(s: GameSnapshot) {
        boardView.visibility = GONE
        board2View.visibility = GONE
        board2Label.visibility = GONE
        seatArea.removeAllViews()
        seatArea.addView(GameUi.seatBar(context, s.seats))
        if (s.community.isNotEmpty()) {
            val disc = GameUi.row(context)
            disc.addView(GameUi.label(context, "弃牌 ", true))
            s.community.takeLast(8).forEach { disc.addView(GameUi.mahjongTile(context, it)) }
            seatArea.addView(disc)
        }
        s.fields["melds"]?.let {
            seatArea.addView(GameUi.label(context, "副露 $it", true))
        }
        handRow.removeAllViews()
        s.hand.forEach { t ->
            handRow.addView(GameUi.mahjongTile(context, t, selected = selectedHand == t) {
                selectedHand = t
                refresh()
            })
        }
        actions.removeAllViews()
        addCommonActions(s)
        if (s.isWaiting) {
            actions.addView(GameUi.btn(context, "开始") { sendMove("start") })
            actions.addView(GameUi.btn(context, "加入") { send("/game join") })
        }
        when (s.fields["canAct"]) {
            "turn" -> {
                actions.addView(GameUi.btn(context, "出牌", enabled = selectedHand != null) {
                    selectedHand?.let { sendMove("discard $it"); selectedHand = null }
                })
                actions.addView(GameUi.btn(context, "杠") {
                    sendMove(if (selectedHand != null) "gang $selectedHand" else "gang")
                })
                actions.addView(GameUi.btn(context, "胡") { sendMove("hu") })
            }
            "claim" -> {
                actions.addView(GameUi.btn(context, "碰") { sendMove("peng") })
                actions.addView(GameUi.btn(context, "杠") { sendMove("gang") })
                actions.addView(GameUi.btn(context, "胡") { sendMove("hu") })
                actions.addView(GameUi.btn(context, "过") { sendMove("pass") })
                actions.addView(GameUi.btn(context, "吃") { promptChi() })
            }
        }
    }

    private fun promptChi() {
        val input = EditText(context).apply {
            hint = "两张牌，如 m2 m3"
            setPadding(dp(16), dp(12), dp(16), dp(12))
        }
        android.app.AlertDialog.Builder(context)
            .setTitle("吃牌（另两张）")
            .setView(input)
            .setPositiveButton("吃") { _, _ ->
                val t = input.text?.toString()?.trim().orEmpty()
                if (t.isNotEmpty()) sendMove("chi $t")
            }
            .setNegativeButton("取消", null)
            .show()
    }

    // ---------- sanguo ----------

    private fun renderSanguo(s: GameSnapshot) {
        boardView.visibility = GONE
        board2View.visibility = GONE
        board2Label.visibility = GONE
        seatArea.removeAllViews()
        seatArea.addView(GameUi.seatBar(context, s.seats))
        if (s.hints.isNotEmpty()) {
            seatArea.addView(GameUi.label(context, s.hints.joinToString("\n"), true))
        }
        handRow.removeAllViews()
        s.hand.forEach { card ->
            handRow.addView(GameUi.pokerCard(context, card, selected = selectedHand == card) {
                selectedHand = if (selectedHand == card) null else card
                refresh()
            })
        }
        // 目标选择
        val targets = GameUi.row(context)
        targets.addView(GameUi.label(context, "目标:", true))
        s.seats.filter { it.name != myName && it.alive }.forEach { seat ->
            targets.addView(GameUi.chip(context, seat.name, selected = selectedTarget == seat.name) {
                selectedTarget = seat.name
                refresh()
            })
        }
        seatArea.addView(targets)

        actions.removeAllViews()
        addCommonActions(s)
        if (s.isWaiting) {
            actions.addView(GameUi.btn(context, "开始") { sendMove("开始"); send("/game show") })
            actions.addView(GameUi.btn(context, "加入") { send("/game join") })
        }
        if (s.isPlaying) {
            actions.addView(GameUi.btn(context, "杀", enabled = selectedTarget != null) {
                val card = selectedHand
                sendMove(if (card != null) "杀 $selectedTarget $card" else "杀 $selectedTarget")
                afterSgs()
            })
            actions.addView(GameUi.btn(context, "闪") { sendMove("闪"); afterSgs() })
            actions.addView(GameUi.btn(context, "桃") { sendMove("桃"); afterSgs() })
            actions.addView(GameUi.btn(context, "决斗", enabled = selectedTarget != null) {
                sendMove("决斗 $selectedTarget"); afterSgs()
            })
            actions.addView(GameUi.btn(context, "过") { sendMove("过"); afterSgs() })
            actions.addView(GameUi.btn(context, "武将") { sendMove("武将") })
        }
    }

    private fun afterSgs() {
        selectedHand = null
        // 三国杀不自动推棋盘，主动刷新
        body.postDelayed({ send("/game show") }, 300)
    }

    // ---------- werewolf ----------

    private fun renderWerewolf(s: GameSnapshot) {
        boardView.visibility = GONE
        board2View.visibility = GONE
        board2Label.visibility = GONE
        seatArea.removeAllViews()
        seatArea.addView(GameUi.seatBar(context, s.seats))
        handRow.removeAllViews()
        s.seats.forEach { seat ->
            handRow.addView(GameUi.chip(context, seat.name, selected = selectedTarget == seat.name) {
                selectedTarget = seat.name
                refresh()
            })
        }
        actions.removeAllViews()
        addCommonActions(s)
        if (s.isWaiting) {
            actions.addView(GameUi.btn(context, "开始") { sendMove("start"); afterWolf() })
            actions.addView(GameUi.btn(context, "加入") { send("/game join") })
        }
        when (s.state.lowercase()) {
            "night" -> {
                actions.addView(GameUi.btn(context, "刀杀", enabled = selectedTarget != null) {
                    sendMove("kill $selectedTarget"); afterWolf()
                })
                actions.addView(GameUi.btn(context, "查验", enabled = selectedTarget != null) {
                    sendMove("check $selectedTarget"); afterWolf()
                })
                actions.addView(GameUi.btn(context, "救人") { sendMove("save"); afterWolf() })
                actions.addView(GameUi.btn(context, "毒人", enabled = selectedTarget != null) {
                    sendMove("poison $selectedTarget"); afterWolf()
                })
                actions.addView(GameUi.btn(context, "过") { sendMove("pass"); afterWolf() })
            }
            "day" -> {
                actions.addView(GameUi.btn(context, "投票", enabled = selectedTarget != null) {
                    sendMove("vote $selectedTarget"); afterWolf()
                })
            }
        }
    }

    private fun afterWolf() {
        body.postDelayed({ send("/game show") }, 300)
    }

    // ---------- common ----------

    private fun addCommonActions(s: GameSnapshot) {
        actions.addView(GameUi.btn(context, "刷新") { send("/game show") })
        actions.addView(GameUi.btn(context, "席位") { send("/game seats") })
        if (s.isPlaying) {
            actions.addView(GameUi.btn(context, "认负") { send("/game resign") })
        }
        actions.addView(GameUi.btn(context, "结束") { send("/game end") })
        if (s.kind == GameKind.GO) {
            actions.addView(GameUi.btn(context, "停一手") { sendMove("pass") })
        }
        if (s.kind == GameKind.REVERSI) {
            actions.addView(GameUi.btn(context, "停一手") { sendMove("pass") })
        }
    }

    private fun addLobbyActions() {
        actions.removeAllViews()
        val games = listOf(
            "xiangqi" to "象棋", "chess" to "国际象棋", "go" to "围棋", "gomoku" to "五子",
            "doushou" to "斗兽", "junqi" to "军棋", "darkchess" to "暗棋", "reversi" to "黑白",
            "battleship" to "海战", "holdem" to "德州", "zjh" to "金花", "niutou" to "牛头",
            "mahjong" to "麻将", "sanguo" to "三国杀", "werewolf" to "狼人",
        )
        games.forEach { (id, label) ->
            actions.addView(GameUi.btn(context, label) { send("/game new $id") })
        }
        actions.addView(GameUi.btn(context, "列表") { send("/game list") })
        actions.addView(GameUi.btn(context, "加入") { send("/game join") })
    }

    private fun sendMove(payload: String) {
        send("/game move $payload")
    }

    private fun send(cmd: String) {
        onSend?.invoke(cmd)
    }

    private fun toast(msg: String) {
        Toast.makeText(context, msg, Toast.LENGTH_SHORT).show()
    }

    private fun dp(v: Int): Int = GameUi.dp(context, v)
}
