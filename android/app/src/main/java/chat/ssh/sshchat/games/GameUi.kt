package chat.ssh.sshchat.games

import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.HorizontalScrollView
import android.widget.LinearLayout
import android.widget.TextView

/** 扑克牌 / 麻将牌 / 座位条等轻量 UI 辅助。 */
object GameUi {
    fun dp(ctx: Context, v: Int): Int = (v * ctx.resources.displayMetrics.density).toInt()

    fun chip(ctx: Context, text: String, selected: Boolean = false, onClick: (() -> Unit)? = null): TextView {
        return TextView(ctx).apply {
            this.text = text
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
            setTextColor(if (selected) Color.WHITE else Color.parseColor("#212121"))
            setPadding(dp(ctx, 10), dp(ctx, 6), dp(ctx, 10), dp(ctx, 6))
            background = GradientDrawable().apply {
                cornerRadius = dp(ctx, 16).toFloat()
                setColor(if (selected) Color.parseColor("#1B5E20") else Color.parseColor("#EEEEEE"))
            }
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ).also { it.marginEnd = dp(ctx, 6); it.bottomMargin = dp(ctx, 4) }
            if (onClick != null) {
                isClickable = true
                setOnClickListener { onClick() }
            }
        }
    }

    fun btn(ctx: Context, text: String, enabled: Boolean = true, onClick: () -> Unit): TextView {
        return TextView(ctx).apply {
            this.text = text
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
            setTextColor(if (enabled) Color.WHITE else Color.parseColor("#BDBDBD"))
            setPadding(dp(ctx, 12), dp(ctx, 8), dp(ctx, 12), dp(ctx, 8))
            background = GradientDrawable().apply {
                cornerRadius = dp(ctx, 6).toFloat()
                setColor(if (enabled) Color.parseColor("#2E7D32") else Color.parseColor("#E0E0E0"))
            }
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ).also { it.marginEnd = dp(ctx, 6); it.bottomMargin = dp(ctx, 4) }
            isEnabled = enabled
            isClickable = enabled
            if (enabled) setOnClickListener { onClick() }
        }
    }

    fun pokerCard(ctx: Context, card: String, selected: Boolean = false, onClick: (() -> Unit)? = null): TextView {
        val red = card.startsWith("♥") || card.startsWith("♦") || card.startsWith("红") || card.startsWith("方")
        return TextView(ctx).apply {
            text = card
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 16f)
            typeface = Typeface.DEFAULT_BOLD
            setTextColor(if (red) Color.parseColor("#C62828") else Color.parseColor("#212121"))
            gravity = Gravity.CENTER
            setPadding(dp(ctx, 8), dp(ctx, 10), dp(ctx, 8), dp(ctx, 10))
            minWidth = dp(ctx, 40)
            background = GradientDrawable().apply {
                cornerRadius = dp(ctx, 6).toFloat()
                setColor(Color.WHITE)
                setStroke(dp(ctx, if (selected) 3 else 1), if (selected) Color.parseColor("#1B5E20") else Color.parseColor("#BDBDBD"))
            }
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ).also { it.marginEnd = dp(ctx, 4) }
            if (onClick != null) {
                isClickable = true
                setOnClickListener { onClick() }
            }
        }
    }

    /** 闷牌背面（炸金花未看牌时）。 */
    fun pokerBack(ctx: Context): TextView {
        return TextView(ctx).apply {
            text = "🂠"
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 18f)
            gravity = Gravity.CENTER
            setPadding(dp(ctx, 10), dp(ctx, 10), dp(ctx, 10), dp(ctx, 10))
            minWidth = dp(ctx, 40)
            setTextColor(Color.parseColor("#E8EAF6"))
            background = GradientDrawable().apply {
                cornerRadius = dp(ctx, 6).toFloat()
                setColor(Color.parseColor("#1A237E"))
                setStroke(dp(ctx, 1), Color.parseColor("#3949AB"))
            }
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ).also { it.marginEnd = dp(ctx, 4) }
        }
    }

    fun mahjongTile(ctx: Context, tile: String, selected: Boolean = false, onClick: (() -> Unit)? = null): TextView {
        return TextView(ctx).apply {
            text = mjLabel(tile)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f)
            typeface = Typeface.DEFAULT_BOLD
            setTextColor(Color.parseColor("#212121"))
            gravity = Gravity.CENTER
            setPadding(dp(ctx, 6), dp(ctx, 8), dp(ctx, 6), dp(ctx, 8))
            minWidth = dp(ctx, 36)
            background = GradientDrawable().apply {
                cornerRadius = dp(ctx, 4).toFloat()
                setColor(if (selected) Color.parseColor("#C8E6C9") else Color.parseColor("#FFFDE7"))
                setStroke(dp(ctx, 1), Color.parseColor("#A1887F"))
            }
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ).also { it.marginEnd = dp(ctx, 3) }
            if (onClick != null) {
                isClickable = true
                setOnClickListener { onClick() }
            }
        }
    }

    fun mjLabel(tile: String): String {
        if (tile.length < 2) return tile
        val n = tile[1]
        return when (tile[0]) {
            'm' -> "${cnDigit(n)}万"
            'p' -> "${cnDigit(n)}筒"
            's' -> "${cnDigit(n)}条"
            'z' -> when (n) {
                '1' -> "东"
                '2' -> "南"
                '3' -> "西"
                '4' -> "北"
                '5' -> "中"
                '6' -> "发"
                '7' -> "白"
                else -> tile
            }
            else -> tile
        }
    }

    private fun cnDigit(c: Char): String = when (c) {
        '1' -> "一"
        '2' -> "二"
        '3' -> "三"
        '4' -> "四"
        '5' -> "五"
        '6' -> "六"
        '7' -> "七"
        '8' -> "八"
        '9' -> "九"
        else -> c.toString()
    }

    fun ntwCard(ctx: Context, n: Int, selected: Boolean = false, onClick: (() -> Unit)? = null): TextView {
        val bulls = when {
            n == 55 -> 7
            n % 11 == 0 -> 5
            n % 10 == 0 -> 3
            n % 5 == 0 -> 2
            else -> 1
        }
        return TextView(ctx).apply {
            text = "$n\n🐂$bulls"
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
            gravity = Gravity.CENTER
            setTextColor(Color.parseColor("#212121"))
            setPadding(dp(ctx, 6), dp(ctx, 6), dp(ctx, 6), dp(ctx, 6))
            minWidth = dp(ctx, 40)
            background = GradientDrawable().apply {
                cornerRadius = dp(ctx, 6).toFloat()
                setColor(if (selected) Color.parseColor("#C8E6C9") else Color.parseColor("#FFF3E0"))
                setStroke(dp(ctx, if (selected) 2 else 1), Color.parseColor("#FF8A65"))
            }
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ).also { it.marginEnd = dp(ctx, 4) }
            if (onClick != null) {
                isClickable = true
                setOnClickListener { onClick() }
            }
        }
    }

    fun hScroll(ctx: Context, content: LinearLayout): HorizontalScrollView =
        HorizontalScrollView(ctx).apply {
            isHorizontalScrollBarEnabled = false
            addView(content)
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            )
        }

    fun row(ctx: Context): LinearLayout =
        LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }

    fun wrapRow(ctx: Context): LinearLayout =
        LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }

    fun label(ctx: Context, text: String, small: Boolean = false): TextView =
        TextView(ctx).apply {
            this.text = text
            setTextSize(TypedValue.COMPLEX_UNIT_SP, if (small) 11f else 13f)
            setTextColor(if (small) Color.parseColor("#757575") else Color.parseColor("#212121"))
            setPadding(0, dp(ctx, 2), 0, dp(ctx, 2))
        }

    fun seatBar(ctx: Context, seats: List<Seat>): View {
        val wrap = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(0, dp(ctx, 4), 0, dp(ctx, 4))
        }
        for (s in seats) {
            wrap.addView(TextView(ctx).apply {
                text = buildString {
                    append(if (s.active) "▸ " else "  ")
                    if (s.index > 0) append("#${s.index} ")
                    append(s.name)
                    if (s.hp != null && s.maxHp != null) append("  ❤️${s.hp}/${s.maxHp}")
                    if (s.detail.isNotEmpty()) append("  ${s.detail}")
                    if (!s.alive) append("  (出局)")
                }
                setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
                setTextColor(
                    when {
                        !s.alive -> Color.parseColor("#9E9E9E")
                        s.active -> Color.parseColor("#1B5E20")
                        else -> Color.parseColor("#424242")
                    },
                )
                typeface = if (s.active) Typeface.DEFAULT_BOLD else Typeface.DEFAULT
            })
        }
        return wrap
    }
}
