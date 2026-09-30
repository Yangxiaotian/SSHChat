package chat.ssh.sshchat.games

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.Typeface
import android.util.AttributeSet
import android.view.MotionEvent
import android.view.View
import kotlin.math.min

/**
 * 通用棋盘绘制：方格 / 交点落子。
 * 单元格 token 约定：
 * - 象棋/暗棋/斗兽："r帅" / "b将" / "?" / ""
 * - 围棋/五子/黑白："B" / "W" / ""
 * - 国际象棋：Unicode 棋子 / ""
 * - 军棋："rC" / "bF" / "?" / ""
 * - 海战："S" / "X" / "o" / "?" / "."
 */
class BoardView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : View(context, attrs) {

    enum class Style { GRID, STONES, XIANGQI, CHESS, JUNQI, SHIP, DOUSHOU }

    var style: Style = Style.GRID
        set(value) {
            field = value
            invalidate()
        }

    var board: Board? = null
        set(value) {
            field = value
            invalidate()
        }

    var selected: Pair<Int, Int>? = null
        set(value) {
            field = value
            invalidate()
        }

    var highlights: Set<Pair<Int, Int>> = emptySet()
        set(value) {
            field = value
            invalidate()
        }

    var onCellTap: ((row: Int, col: Int) -> Unit)? = null

    private val woodBg = Color.parseColor("#DEB887")
    private val woodDark = Color.parseColor("#C4A574")
    private val linePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#5D4037")
        strokeWidth = 1.5f
        style = Paint.Style.STROKE
    }
    private val thickLine = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#3E2723")
        strokeWidth = 2.5f
        style = Paint.Style.STROKE
    }
    private val fillPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val textPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        textAlign = Paint.Align.CENTER
        typeface = Typeface.DEFAULT_BOLD
    }
    private val lastPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#FF5722")
        style = Paint.Style.STROKE
        strokeWidth = 3f
    }
    private val selPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#4CAF50")
        style = Paint.Style.STROKE
        strokeWidth = 4f
    }
    private val hiPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#66BB6A")
        alpha = 90
        style = Paint.Style.FILL
    }

    var maxHeightFraction: Float = 0.48f
        set(value) {
            field = value
            requestLayout()
        }

    private var cell = 40f
    private var ox = 0f
    private var oy = 0f

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val b = board
        val w = MeasureSpec.getSize(widthMeasureSpec)
        val hMode = MeasureSpec.getMode(heightMeasureSpec)
        val hSize = MeasureSpec.getSize(heightMeasureSpec)
        if (b == null || b.cols <= 0 || b.rows <= 0) {
            val fallback = when (hMode) {
                MeasureSpec.EXACTLY -> hSize
                MeasureSpec.AT_MOST -> min(hSize, (w * 0.6f).toInt().coerceAtLeast(120))
                else -> (w * 0.6f).toInt().coerceAtLeast(120)
            }
            setMeasuredDimension(w, fallback)
            return
        }
        val pad = dp(8f)
        val avail = (w - pad * 2).coerceAtLeast(1f)
        val wantH = if (style == Style.XIANGQI && b.cols > 1 && b.rows > 1) {
            // 交点棋盘：宽跨 cols-1 格，高跨 rows-1 格（9×10 点 → 8×9）
            val cellW = avail / (b.cols - 1)
            cellW * (b.rows - 1) + pad * 2
        } else {
            val cellW = avail / b.cols
            cellW * b.rows + pad * 2
        }
        val maxH = resources.displayMetrics.heightPixels * maxHeightFraction
        val h = when (hMode) {
            MeasureSpec.EXACTLY -> hSize.toFloat()
            MeasureSpec.AT_MOST -> min(wantH, min(maxH, hSize.toFloat()))
            else -> min(wantH, maxH)
        }
        setMeasuredDimension(w, h.toInt().coerceAtLeast(120))
    }

    override fun onDraw(canvas: Canvas) {
        val b = board ?: return
        if (b.rows <= 0 || b.cols <= 0) return
        val pad = dp(8f)
        if (style == Style.XIANGQI && b.cols > 1 && b.rows > 1) {
            cell = min((width - pad * 2) / (b.cols - 1), (height - pad * 2) / (b.rows - 1))
            ox = (width - cell * (b.cols - 1)) / 2f
            oy = (height - cell * (b.rows - 1)) / 2f
        } else {
            cell = min((width - pad * 2) / b.cols, (height - pad * 2) / b.rows)
            ox = (width - cell * b.cols) / 2f
            oy = (height - cell * b.rows) / 2f
        }

        when (style) {
            Style.XIANGQI -> drawXiangqi(canvas, b)
            Style.STONES -> drawStones(canvas, b)
            Style.CHESS -> drawChess(canvas, b)
            Style.JUNQI -> drawJunqi(canvas, b)
            Style.SHIP -> drawShip(canvas, b)
            Style.DOUSHOU -> drawDoushou(canvas, b)
            Style.GRID -> drawGrid(canvas, b)
        }
        selected?.let { (r, c) ->
            if (r in 0 until b.rows && c in 0 until b.cols) {
                if (style == Style.XIANGQI) {
                    val cx = ox + c * cell
                    val cy = oy + r * cell
                    canvas.drawCircle(cx, cy, cell * 0.42f, selPaint)
                } else {
                    canvas.drawRect(ox + c * cell, oy + r * cell, ox + (c + 1) * cell, oy + (r + 1) * cell, selPaint)
                }
            }
        }
        for ((r, c) in highlights) {
            if (r in 0 until b.rows && c in 0 until b.cols) {
                if (style == Style.XIANGQI) {
                    val cx = ox + c * cell
                    val cy = oy + r * cell
                    fillPaint.color = Color.parseColor("#404CAF50")
                    canvas.drawCircle(cx, cy, cell * 0.2f, fillPaint)
                } else {
                    canvas.drawRect(ox + c * cell, oy + r * cell, ox + (c + 1) * cell, oy + (r + 1) * cell, hiPaint)
                }
            }
        }
    }

    private fun drawXiangqi(canvas: Canvas, b: Board) {
        // 背景略大于棋盘，给最外圈棋子留边
        val margin = cell * 0.55f
        fillPaint.color = woodBg
        canvas.drawRect(ox - margin, oy - margin, ox + (b.cols - 1) * cell + margin, oy + (b.rows - 1) * cell + margin, fillPaint)

        // 10 条横线
        for (r in 0 until b.rows) {
            val y = oy + r * cell
            val paint = if (r == 0 || r == b.rows - 1) thickLine else linePaint
            canvas.drawLine(ox, y, ox + (b.cols - 1) * cell, y, paint)
        }
        // 9 条竖线：左右贯通，中间在楚河处断开
        for (c in 0 until b.cols) {
            val x = ox + c * cell
            val paint = if (c == 0 || c == b.cols - 1) thickLine else linePaint
            if (c == 0 || c == b.cols - 1 || b.rows < 10) {
                canvas.drawLine(x, oy, x, oy + (b.rows - 1) * cell, paint)
            } else {
                canvas.drawLine(x, oy, x, oy + 4 * cell, paint)
                canvas.drawLine(x, oy + 5 * cell, x, oy + (b.rows - 1) * cell, paint)
            }
        }

        // 九宫斜线（米字的交叉对角）
        if (b.rows >= 10 && b.cols >= 9) {
            canvas.drawLine(ox + 3 * cell, oy, ox + 5 * cell, oy + 2 * cell, linePaint)
            canvas.drawLine(ox + 5 * cell, oy, ox + 3 * cell, oy + 2 * cell, linePaint)
            canvas.drawLine(ox + 3 * cell, oy + 7 * cell, ox + 5 * cell, oy + 9 * cell, linePaint)
            canvas.drawLine(ox + 5 * cell, oy + 7 * cell, ox + 3 * cell, oy + 9 * cell, linePaint)
        }

        // 炮位 / 兵位角标
        if (b.rows >= 10 && b.cols >= 9) {
            val marks = listOf(
                2 to 1, 2 to 7, 7 to 1, 7 to 7, // 炮
                3 to 0, 3 to 2, 3 to 4, 3 to 6, 3 to 8, // 黑兵
                6 to 0, 6 to 2, 6 to 4, 6 to 6, 6 to 8, // 红兵
            )
            for ((r, c) in marks) {
                drawXiangqiCornerMark(canvas, ox + c * cell, oy + r * cell)
            }
        }

        // 楚河汉界
        if (b.rows >= 10) {
            val midY = oy + 4.5f * cell
            textPaint.color = Color.parseColor("#5D4037")
            textPaint.textSize = cell * 0.42f
            canvas.drawText("楚 河", ox + 2 * cell, midY + textPaint.textSize * 0.35f, textPaint)
            canvas.drawText("汉 界", ox + 6 * cell, midY + textPaint.textSize * 0.35f, textPaint)
        }

        // 棋子画在交点
        textPaint.textSize = cell * 0.48f
        val pieceR = cell * 0.38f
        for (r in 0 until b.rows) {
            for (c in 0 until b.cols) {
                val tok = b.at(r, c)
                if (tok.isEmpty()) continue
                val cx = ox + c * cell
                val cy = oy + r * cell
                val red = tok.startsWith("r")
                fillPaint.color = if (red) Color.parseColor("#FFF8E1") else Color.parseColor("#212121")
                canvas.drawCircle(cx, cy, pieceR, fillPaint)
                fillPaint.style = Paint.Style.STROKE
                fillPaint.strokeWidth = 2f
                fillPaint.color = if (red) Color.parseColor("#C62828") else Color.parseColor("#FAFAFA")
                canvas.drawCircle(cx, cy, pieceR, fillPaint)
                fillPaint.style = Paint.Style.FILL
                textPaint.color = if (red) Color.parseColor("#C62828") else Color.WHITE
                canvas.drawText(tok.drop(1), cx, cy + textPaint.textSize * 0.35f, textPaint)
                if (b.isLast(r, c)) canvas.drawCircle(cx, cy, pieceR + 2f, lastPaint)
            }
        }
    }

    /** 炮/兵位的四角小折线标记。 */
    private fun drawXiangqiCornerMark(canvas: Canvas, cx: Float, cy: Float) {
        val s = cell * 0.14f
        val g = cell * 0.08f
        val p = linePaint
        // 左上
        canvas.drawLine(cx - g - s, cy - g, cx - g, cy - g, p)
        canvas.drawLine(cx - g, cy - g - s, cx - g, cy - g, p)
        // 右上
        canvas.drawLine(cx + g, cy - g, cx + g + s, cy - g, p)
        canvas.drawLine(cx + g, cy - g - s, cx + g, cy - g, p)
        // 左下
        canvas.drawLine(cx - g - s, cy + g, cx - g, cy + g, p)
        canvas.drawLine(cx - g, cy + g, cx - g, cy + g + s, p)
        // 右下
        canvas.drawLine(cx + g, cy + g, cx + g + s, cy + g, p)
        canvas.drawLine(cx + g, cy + g, cx + g, cy + g + s, p)
    }

    private fun drawStones(canvas: Canvas, b: Board) {
        fillPaint.color = woodDark
        canvas.drawRect(ox, oy, ox + b.cols * cell, oy + b.rows * cell, fillPaint)
        // 交点网格
        for (r in 0 until b.rows) {
            val y = oy + (r + 0.5f) * cell
            canvas.drawLine(ox + 0.5f * cell, y, ox + (b.cols - 0.5f) * cell, y, linePaint)
        }
        for (c in 0 until b.cols) {
            val x = ox + (c + 0.5f) * cell
            canvas.drawLine(x, oy + 0.5f * cell, x, oy + (b.rows - 0.5f) * cell, linePaint)
        }
        // 星位（19/15/9/8）
        val stars = when (b.rows) {
            19 -> listOf(3, 9, 15)
            15 -> listOf(3, 7, 11)
            9 -> listOf(2, 4, 6)
            else -> emptyList()
        }
        fillPaint.color = Color.parseColor("#3E2723")
        for (r in stars) for (c in stars) {
            canvas.drawCircle(ox + (c + 0.5f) * cell, oy + (r + 0.5f) * cell, cell * 0.08f, fillPaint)
        }
        for (r in 0 until b.rows) {
            for (c in 0 until b.cols) {
                val tok = b.at(r, c)
                if (tok.isEmpty()) continue
                val cx = ox + (c + 0.5f) * cell
                val cy = oy + (r + 0.5f) * cell
                fillPaint.color = if (tok == "B") Color.BLACK else Color.WHITE
                canvas.drawCircle(cx, cy, cell * 0.42f, fillPaint)
                if (tok == "W") {
                    fillPaint.style = Paint.Style.STROKE
                    fillPaint.color = Color.GRAY
                    fillPaint.strokeWidth = 1.5f
                    canvas.drawCircle(cx, cy, cell * 0.42f, fillPaint)
                    fillPaint.style = Paint.Style.FILL
                }
                if (b.isLast(r, c)) canvas.drawCircle(cx, cy, cell * 0.18f, lastPaint)
            }
        }
    }

    private fun drawChess(canvas: Canvas, b: Board) {
        for (r in 0 until b.rows) {
            for (c in 0 until b.cols) {
                fillPaint.color = if ((r + c) % 2 == 0) Color.parseColor("#F0D9B5") else Color.parseColor("#B58863")
                canvas.drawRect(ox + c * cell, oy + r * cell, ox + (c + 1) * cell, oy + (r + 1) * cell, fillPaint)
                val tok = b.at(r, c)
                if (tok.isNotEmpty()) {
                    textPaint.color = Color.BLACK
                    textPaint.textSize = cell * 0.72f
                    val cx = ox + (c + 0.5f) * cell
                    val cy = oy + (r + 0.5f) * cell + textPaint.textSize * 0.35f
                    canvas.drawText(tok, cx, cy, textPaint)
                }
                if (b.isLast(r, c)) {
                    canvas.drawRect(
                        ox + c * cell + 2, oy + r * cell + 2,
                        ox + (c + 1) * cell - 2, oy + (r + 1) * cell - 2, lastPaint,
                    )
                }
            }
        }
    }

    private fun drawJunqi(canvas: Canvas, b: Board) {
        fillPaint.color = Color.parseColor("#E8F5E9")
        canvas.drawRect(ox, oy, ox + b.cols * cell, oy + b.rows * cell, fillPaint)
        // 中立带
        if (b.rows == 12) {
            fillPaint.color = Color.parseColor("#BBDEFB")
            canvas.drawRect(ox, oy + 5 * cell, ox + b.cols * cell, oy + 7 * cell, fillPaint)
        }
        for (r in 0..b.rows) {
            canvas.drawLine(ox, oy + r * cell, ox + b.cols * cell, oy + r * cell, linePaint)
        }
        for (c in 0..b.cols) {
            canvas.drawLine(ox + c * cell, oy, ox + c * cell, oy + b.rows * cell, linePaint)
        }
        textPaint.textSize = cell * 0.45f
        for (r in 0 until b.rows) {
            for (c in 0 until b.cols) {
                val tok = b.at(r, c)
                if (tok.isEmpty()) continue
                val cx = ox + (c + 0.5f) * cell
                val cy = oy + (r + 0.5f) * cell
                when {
                    tok == "?" -> {
                        fillPaint.color = Color.parseColor("#9E9E9E")
                        canvas.drawRoundRect(RectF(cx - cell * 0.38f, cy - cell * 0.32f, cx + cell * 0.38f, cy + cell * 0.32f), 6f, 6f, fillPaint)
                        textPaint.color = Color.WHITE
                        canvas.drawText("?", cx, cy + textPaint.textSize * 0.35f, textPaint)
                    }
                    else -> {
                        val red = tok.startsWith("r")
                        fillPaint.color = if (red) Color.parseColor("#E53935") else Color.parseColor("#1E88E5")
                        canvas.drawRoundRect(RectF(cx - cell * 0.38f, cy - cell * 0.32f, cx + cell * 0.38f, cy + cell * 0.32f), 6f, 6f, fillPaint)
                        textPaint.color = Color.WHITE
                        canvas.drawText(junqiLabel(tok.drop(1)), cx, cy + textPaint.textSize * 0.35f, textPaint)
                    }
                }
                if (b.isLast(r, c)) {
                    canvas.drawRoundRect(
                        RectF(cx - cell * 0.4f, cy - cell * 0.34f, cx + cell * 0.4f, cy + cell * 0.34f),
                        6f, 6f, lastPaint,
                    )
                }
            }
        }
    }

    private fun junqiLabel(code: String): String = when (code) {
        "F" -> "旗"
        "C" -> "司"
        "A" -> "军"
        "D" -> "师"
        "B" -> "旅"
        "R" -> "团"
        "T" -> "营"
        "N" -> "连"
        "P" -> "排"
        "E" -> "工"
        "M" -> "雷"
        "O" -> "炸"
        else -> code
    }

    private fun drawShip(canvas: Canvas, b: Board) {
        fillPaint.color = Color.parseColor("#0277BD")
        canvas.drawRect(ox, oy, ox + b.cols * cell, oy + b.rows * cell, fillPaint)
        for (r in 0..b.rows) canvas.drawLine(ox, oy + r * cell, ox + b.cols * cell, oy + r * cell, linePaint)
        for (c in 0..b.cols) canvas.drawLine(ox + c * cell, oy, ox + c * cell, oy + b.rows * cell, linePaint)
        textPaint.textSize = cell * 0.4f
        for (r in 0 until b.rows) {
            for (c in 0 until b.cols) {
                val tok = b.at(r, c)
                val cx = ox + (c + 0.5f) * cell
                val cy = oy + (r + 0.5f) * cell
                when (tok) {
                    "S" -> {
                        fillPaint.color = Color.parseColor("#78909C")
                        canvas.drawRect(ox + c * cell + 2, oy + r * cell + 2, ox + (c + 1) * cell - 2, oy + (r + 1) * cell - 2, fillPaint)
                    }
                    "X" -> {
                        fillPaint.color = Color.parseColor("#D32F2F")
                        canvas.drawCircle(cx, cy, cell * 0.3f, fillPaint)
                    }
                    "o" -> {
                        fillPaint.color = Color.parseColor("#B3E5FC")
                        canvas.drawCircle(cx, cy, cell * 0.18f, fillPaint)
                    }
                    "?" -> {
                        textPaint.color = Color.parseColor("#81D4FA")
                        canvas.drawText("·", cx, cy + textPaint.textSize * 0.3f, textPaint)
                    }
                }
                if (b.isLast(r, c)) {
                    canvas.drawRect(ox + c * cell + 1, oy + r * cell + 1, ox + (c + 1) * cell - 1, oy + (r + 1) * cell - 1, lastPaint)
                }
            }
        }
    }

    /** 斗兽棋固定地形（0-based 绝对坐标，与服务端一致）。 */
    private fun doushouTerrain(absRow: Int, absCol: Int): String = when {
        absRow == 0 && absCol == 3 -> "黑穴"
        absRow == 8 && absCol == 3 -> "红穴"
        absRow == 0 && (absCol == 2 || absCol == 4) -> "黑陷"
        absRow == 1 && absCol == 3 -> "黑陷"
        absRow == 8 && (absCol == 2 || absCol == 4) -> "红陷"
        absRow == 7 && absCol == 3 -> "红陷"
        absRow in 3..5 && absCol in setOf(1, 2, 4, 5) -> "河"
        else -> ""
    }

    private fun drawDoushou(canvas: Canvas, b: Board) {
        fillPaint.color = Color.parseColor("#DEBE8A")
        canvas.drawRect(ox, oy, ox + b.cols * cell, oy + b.rows * cell, fillPaint)
        textPaint.typeface = Typeface.DEFAULT_BOLD
        for (r in 0 until b.rows) {
            for (c in 0 until b.cols) {
                val absR = if (r < b.rowLabels.size) b.rowLabels[r] - 1 else r
                val absC = if (c < b.colLabels.size) (b.colLabels[c].toIntOrNull() ?: (c + 1)) - 1 else c
                val terrain = b.at(r, c).takeIf {
                    it in setOf("河", "红穴", "黑穴", "红陷", "黑陷")
                } ?: doushouTerrain(absR, absC)
                val left = ox + c * cell
                val top = oy + r * cell
                val rect = RectF(left + 1f, top + 1f, left + cell - 1f, top + cell - 1f)
                fillPaint.color = when {
                    terrain == "河" -> Color.parseColor("#81D4FA")
                    terrain.endsWith("穴") -> Color.parseColor("#FFE082")
                    terrain.endsWith("陷") -> Color.parseColor("#EF9A9A")
                    else -> Color.parseColor("#E8C992")
                }
                canvas.drawRoundRect(rect, 4f, 4f, fillPaint)
                linePaint.color = Color.parseColor("#8D6E63")
                canvas.drawRoundRect(rect, 4f, 4f, linePaint)

                val tok = b.at(r, c)
                val cx = left + cell / 2f
                val cy = top + cell / 2f
                if (tok.startsWith("r") || tok.startsWith("b")) {
                    val red = tok.startsWith("r")
                    fillPaint.color = if (red) Color.parseColor("#FFF8E1") else Color.parseColor("#212121")
                    canvas.drawCircle(cx, cy, cell * 0.36f, fillPaint)
                    textPaint.textSize = cell * 0.42f
                    textPaint.color = if (red) Color.parseColor("#C62828") else Color.WHITE
                    canvas.drawText(tok.drop(1), cx, cy + textPaint.textSize * 0.35f, textPaint)
                } else if (terrain.isNotEmpty()) {
                    textPaint.textSize = cell * 0.28f
                    textPaint.color = Color.parseColor("#5D4037")
                    canvas.drawText(terrain, cx, cy + textPaint.textSize * 0.35f, textPaint)
                }
                if (b.isLast(r, c)) canvas.drawCircle(cx, cy, cell * 0.4f, lastPaint)
            }
        }
        linePaint.color = Color.parseColor("#5D4037")
    }

    private fun drawGrid(canvas: Canvas, b: Board) {
        fillPaint.color = woodBg
        canvas.drawRect(ox, oy, ox + b.cols * cell, oy + b.rows * cell, fillPaint)
        for (r in 0..b.rows) canvas.drawLine(ox, oy + r * cell, ox + b.cols * cell, oy + r * cell, linePaint)
        for (c in 0..b.cols) canvas.drawLine(ox + c * cell, oy, ox + c * cell, oy + b.rows * cell, linePaint)
        textPaint.textSize = cell * 0.5f
        for (r in 0 until b.rows) {
            for (c in 0 until b.cols) {
                val tok = b.at(r, c)
                if (tok.isEmpty()) continue
                val cx = ox + (c + 0.5f) * cell
                val cy = oy + (r + 0.5f) * cell
                when {
                    tok == "?" -> {
                        fillPaint.color = Color.parseColor("#9E9E9E")
                        canvas.drawCircle(cx, cy, cell * 0.38f, fillPaint)
                        textPaint.color = Color.WHITE
                        canvas.drawText("?", cx, cy + textPaint.textSize * 0.35f, textPaint)
                    }
                    tok.startsWith("r") || tok.startsWith("b") -> {
                        val red = tok.startsWith("r")
                        fillPaint.color = if (red) Color.parseColor("#FFF8E1") else Color.parseColor("#212121")
                        canvas.drawCircle(cx, cy, cell * 0.4f, fillPaint)
                        textPaint.color = if (red) Color.parseColor("#C62828") else Color.WHITE
                        canvas.drawText(tok.drop(1), cx, cy + textPaint.textSize * 0.35f, textPaint)
                    }
                    else -> {
                        textPaint.color = Color.BLACK
                        canvas.drawText(tok, cx, cy + textPaint.textSize * 0.35f, textPaint)
                    }
                }
                if (b.isLast(r, c)) canvas.drawCircle(cx, cy, cell * 0.42f, lastPaint)
            }
        }
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        if (event.action != MotionEvent.ACTION_UP) return true
        val b = board ?: return true
        if (cell <= 0f) return true
        val (r, c) = if (style == Style.XIANGQI) {
            kotlin.math.round((event.y - oy) / cell).toInt().coerceIn(0, b.rows - 1) to
                kotlin.math.round((event.x - ox) / cell).toInt().coerceIn(0, b.cols - 1)
        } else {
            ((event.y - oy) / cell).toInt() to ((event.x - ox) / cell).toInt()
        }
        if (r in 0 until b.rows && c in 0 until b.cols) {
            onCellTap?.invoke(r, c)
            performClick()
        }
        return true
    }

    override fun performClick(): Boolean {
        super.performClick()
        return true
    }

    private fun dp(v: Float): Float = v * resources.displayMetrics.density
}
