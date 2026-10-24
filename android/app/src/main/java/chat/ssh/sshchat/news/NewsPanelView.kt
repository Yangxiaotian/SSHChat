package chat.ssh.sshchat.news

import android.content.Context
import android.content.Intent
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.util.AttributeSet
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast

/**
 * Full-screen news list. Tap a row to open the RSS original URL in the browser.
 */
class NewsPanelView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : FrameLayout(context, attrs) {

    var onSend: ((String) -> Boolean)? = null

    private val accent = 0xFF1B5E20.toInt()
    private val ink = 0xFF2B2B2B.toInt()
    private val sub = 0xFF8A8A8A.toInt()
    private val chrome = 0xFFFFFFFF.toInt()
    private val bg = 0xFFFAF9F6.toInt()

    private val cats = listOf("全部" to "", "中文" to "中文", "国际" to "国际", "科技" to "科技")
    private var filter = ""
    private var items: List<NewsItem> = emptyList()
    private var visible = false
    private var requestPending = false

    private val main = Handler(Looper.getMainLooper())
    private val requestTimeout = Runnable {
        if (requestPending) {
            setLoading(false)
            toast("服务器没有响应，请重试")
        }
    }

    private val header = LinearLayout(context)
    private val back = TextView(context)
    private val title = TextView(context)
    private val refresh = TextView(context)
    private val chipRow = LinearLayout(context)
    private val summary = TextView(context)
    private val progress = ProgressBar(context)
    private val scroll = ScrollView(context)
    private val list = LinearLayout(context)
    private val empty = TextView(context)

    init {
        visibility = GONE
        isClickable = true
        isFocusable = true
        setBackgroundColor(bg)
        build()
    }

    fun isActive(): Boolean = visible

    fun open() {
        visible = true
        visibility = VISIBLE
        requestNews()
    }

    fun hide() {
        if (!visible) return
        visible = false
        visibility = GONE
        setLoading(false)
    }

    fun reset() {
        hide()
        items = emptyList()
        filter = ""
        render()
    }

    fun handleBack(): Boolean {
        if (!visible) return false
        hide()
        return true
    }

    fun handle(events: List<NewsEvent>) {
        var changed = false
        for (ev in events) {
            when (ev) {
                is NewsEvent.Items -> {
                    items = ev.items
                    changed = true
                    setLoading(false)
                }
                is NewsEvent.Notice -> {
                    if (items.isEmpty()) summary.text = ev.text
                    setLoading(false)
                }
            }
        }
        if (changed) render()
    }

    private fun requestNews() {
        if (onSend?.invoke("/news") != true) return
        items = emptyList()
        render()
        setLoading(true)
        summary.text = "正在获取新闻…"
        empty.visibility = GONE
    }

    private fun build() {
        val root = LinearLayout(context).apply { orientation = LinearLayout.VERTICAL }

        header.orientation = LinearLayout.HORIZONTAL
        header.gravity = Gravity.CENTER_VERTICAL
        header.minimumHeight = dp(52)
        header.setBackgroundColor(chrome)
        back.apply {
            this.text = "‹ 聊天"
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 16f)
            setTextColor(accent)
            setPadding(dp(14), dp(10), dp(14), dp(10))
            setOnClickListener { hide() }
        }
        title.apply {
            this.text = "新闻"
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 18f)
            typeface = Typeface.DEFAULT_BOLD
            setTextColor(ink)
            gravity = Gravity.CENTER
        }
        refresh.apply {
            this.text = "刷新"
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
            setTextColor(accent)
            setPadding(dp(14), dp(10), dp(14), dp(10))
            gravity = Gravity.END
            setOnClickListener { requestNews() }
        }
        header.addView(back, LinearLayout.LayoutParams(dp(84), LinearLayout.LayoutParams.WRAP_CONTENT))
        header.addView(title, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        header.addView(refresh, LinearLayout.LayoutParams(dp(84), LinearLayout.LayoutParams.WRAP_CONTENT))
        root.addView(header)

        chipRow.orientation = LinearLayout.HORIZONTAL
        chipRow.setPadding(dp(12), dp(8), dp(12), dp(4))
        cats.forEach { (label, token) ->
            chipRow.addView(chip(label, token), LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f).also {
                it.marginEnd = dp(6)
            })
        }
        root.addView(chipRow)

        val summaryRow = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(16), dp(4), dp(16), dp(8))
        }
        summary.apply {
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
            setTextColor(sub)
        }
        progress.isIndeterminate = true
        progress.visibility = GONE
        summaryRow.addView(summary, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        summaryRow.addView(progress, LinearLayout.LayoutParams(dp(18), dp(18)))
        root.addView(summaryRow)

        list.orientation = LinearLayout.VERTICAL
        list.setPadding(dp(12), 0, dp(12), dp(24))
        empty.apply {
            this.text = "点一行打开原文网页"
            setTextColor(sub)
            gravity = Gravity.CENTER
            setPadding(0, dp(48), 0, dp(48))
        }
        list.addView(empty)
        scroll.addView(list)
        root.addView(scroll, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f))
        addView(root, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT))
        paintChips()
    }

    private fun chip(label: String, token: String): TextView = TextView(context).apply {
        this.text = label
        gravity = Gravity.CENTER
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
        setPadding(dp(6), dp(8), dp(6), dp(8))
        setOnClickListener {
            filter = token
            paintChips()
            render()
        }
        tag = token
    }

    private fun paintChips() {
        for (i in 0 until chipRow.childCount) {
            val tv = chipRow.getChildAt(i) as? TextView ?: continue
            val on = (tv.tag as? String).orEmpty() == filter
            tv.setTextColor(if (on) 0xFFFFFFFF.toInt() else accent)
            tv.background = rounded(if (on) accent else chrome, dp(16).toFloat(), accent)
        }
    }

    private fun render() {
        val shown = if (filter.isEmpty()) items else items.filter { it.categoryToken == filter }
        summary.text = when {
            requestPending && items.isEmpty() -> "正在获取新闻…"
            shown.isEmpty() -> "暂无新闻，点刷新"
            else -> "共 ${shown.size} 条 · 点一行打开原文网页"
        }
        list.removeAllViews()
        if (shown.isEmpty()) {
            list.addView(empty)
            empty.visibility = VISIBLE
            return
        }
        empty.visibility = GONE
        var lastCat = ""
        for (item in shown) {
            if (item.categoryLabel.isNotEmpty() && item.categoryLabel != lastCat) {
                lastCat = item.categoryLabel
                list.addView(TextView(context).apply {
                    text = lastCat
                    setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
                    typeface = Typeface.DEFAULT_BOLD
                    setTextColor(sub)
                    setPadding(dp(6), dp(14), dp(6), dp(6))
                })
            }
            list.addView(row(item))
        }
    }

    private fun row(item: NewsItem): View {
        val card = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(12), dp(12), dp(12), dp(12))
            background = rounded(chrome, dp(10).toFloat(), 0xFFE0E0E0.toInt())
            isClickable = true
            isFocusable = true
            setOnClickListener { openOriginal(item) }
        }
        val lp = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT,
        ).also { it.bottomMargin = dp(8) }
        card.addView(TextView(context).apply {
            text = "${item.source}  ·  #${item.index}"
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
            setTextColor(sub)
        })
        card.addView(TextView(context).apply {
            text = item.title
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 16f)
            setTextColor(ink)
            setPadding(0, dp(4), 0, 0)
        })
        if (item.body.isNotBlank()) {
            card.addView(TextView(context).apply {
                text = item.body.trim()
                setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
                setTextColor(sub)
                maxLines = 3
                ellipsize = android.text.TextUtils.TruncateAt.END
                setPadding(0, dp(6), 0, 0)
            })
        }
        card.addView(TextView(context).apply {
            text = if (item.link.isNotEmpty()) "打开原文 ›" else "无原文链接"
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
            setTextColor(if (item.link.isNotEmpty()) accent else sub)
            setPadding(0, dp(8), 0, 0)
        })
        card.layoutParams = lp
        return card
    }

    private fun openOriginal(item: NewsItem) {
        val raw = item.link.trim()
        if (raw.isEmpty() || !(raw.startsWith("http://") || raw.startsWith("https://"))) {
            toast("这条没有原文链接")
            return
        }
        try {
            context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(raw)))
        } catch (_: Exception) {
            toast("无法打开链接")
        }
    }

    private fun setLoading(on: Boolean) {
        requestPending = on
        main.removeCallbacks(requestTimeout)
        progress.visibility = if (on) VISIBLE else GONE
        if (on) main.postDelayed(requestTimeout, 20_000)
    }

    private fun toast(msg: String) {
        Toast.makeText(context, msg, Toast.LENGTH_SHORT).show()
    }

    private fun rounded(fill: Int, radius: Float, stroke: Int?): GradientDrawable =
        GradientDrawable().apply {
            setColor(fill)
            cornerRadius = radius
            if (stroke != null) setStroke(dp(1), stroke)
        }

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()
}
