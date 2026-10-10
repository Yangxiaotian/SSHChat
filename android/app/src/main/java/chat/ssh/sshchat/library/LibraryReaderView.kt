package chat.ssh.sshchat.library

import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.text.Editable
import android.text.InputType
import android.text.Layout
import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.TextWatcher
import android.text.style.RelativeSizeSpan
import android.text.style.StyleSpan
import android.util.AttributeSet
import android.util.TypedValue
import android.view.ActionMode
import android.view.GestureDetector
import android.view.Gravity
import android.view.Menu
import android.view.MenuItem
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.ScrollView
import android.widget.SeekBar
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import kotlin.math.abs
import kotlin.math.roundToInt

/**
 * Full-screen native reader for `/library`: catalog list + paginated reading page.
 * Text arrives as server `[*]` lines; [LibraryParser] turns them into [LibraryEvent]s.
 */
class LibraryReaderView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : FrameLayout(context, attrs) {

    /** @return false when not connected (the command was dropped). */
    var onSend: ((String) -> Boolean)? = null
    /** Reader shown/hidden, or theme changed while shown: (visible, backgroundColor, isLightBackground). */
    var onChromeChanged: ((Boolean, Int, Boolean) -> Unit)? = null

    private enum class Mode { HIDDEN, CATALOG, READING }

    private data class Theme(val name: String, val bg: Int, val text: Int, val sub: Int, val chrome: Int, val accent: Int, val light: Boolean)

    private val themes = listOf(
        Theme("日间", 0xFFFAF9F6.toInt(), 0xFF2B2B2B.toInt(), 0xFF8A8A8A.toInt(), 0xFFFFFFFF.toInt(), 0xFF1B5E20.toInt(), true),
        Theme("护眼", 0xFFF4ECD8.toInt(), 0xFF5B4636.toInt(), 0xFF9C8B75.toInt(), 0xFFEFE4CB.toInt(), 0xFF8D5524.toInt(), true),
        Theme("豆沙绿", 0xFFCCE8CF.toInt(), 0xFF2E3B2F.toInt(), 0xFF5F7A61.toInt(), 0xFFBEDDC1.toInt(), 0xFF2E7D32.toInt(), true),
        Theme("夜间", 0xFF141414.toInt(), 0xFF9E9E9E.toInt(), 0xFF5E5E5E.toInt(), 0xFF1F1F1F.toInt(), 0xFF81C784.toInt(), false),
    )

    private val prefs = context.getSharedPreferences("library_reader", Context.MODE_PRIVATE)
    private var fontSp = prefs.getFloat("font_sp", 19f)
    private var lineMult = prefs.getFloat("line_mult", 1.75f)
    private var themeIdx = prefs.getInt("theme", 0).coerceIn(0, themes.lastIndex)
    private var serif = prefs.getBoolean("serif", true)
    private val theme get() = themes[themeIdx]

    private var mode = Mode.HIDDEN
    private var books: List<LibraryBook> = emptyList()
    private var catalogNotes: List<String> = emptyList()
    private var query = ""
    private var page: LibraryPage? = null
    private var currentBook: LibraryBook? = null
    private var reopenTried = false
    private var landAtEnd = false
    private var chromeVisible = false
    private var settingsVisible = false
    private var requestPending = false

    private val main = Handler(Looper.getMainLooper())
    private val requestTimeout = Runnable {
        if (requestPending) {
            setLoading(false)
            toast("服务器没有响应，请重试")
        }
    }

    private val dict = DictCapture()
    private var dictWord = ""
    private var dictDialog: AlertDialog? = null
    private val dictFinish = Runnable {
        val lines = dict.finish() ?: return@Runnable
        main.removeCallbacks(dictTimeout)
        showDict(formatDict(lines))
    }
    private val dictTimeout = Runnable {
        if (dict.isActive) {
            dict.cancel()
            showDict("词典没有响应，请重试")
        }
    }
    private var selectionAtDown = false

    // --- catalog pane ---
    private val catalogPane = LinearLayout(context)
    private val catalogHeader = LinearLayout(context)
    private val catalogTitle = TextView(context)
    private val catalogBack = TextView(context)
    private val catalogRefresh = TextView(context)
    private val searchInput = EditText(context)
    private val catalogSummary = TextView(context)
    private val catalogScroll = ScrollView(context)
    private val catalogList = LinearLayout(context)
    private val catalogProgress = ProgressBar(context)

    // --- reading pane ---
    private val readingPane = FrameLayout(context)
    private val bodyScroll = object : ScrollView(context) {
        // The selectable body grabs focus on tap; never jump the page to "reveal" it.
        override fun computeScrollDeltaToGetChildRectOnScreen(rect: android.graphics.Rect?): Int = 0
    }
    private val bodyColumn = LinearLayout(context)
    private val pageHeading = TextView(context)
    private val bodyText = TextView(context)
    private val pageEnd = TextView(context)
    private val nextPageBtn = TextView(context)
    private val statusBar = LinearLayout(context)
    private val statusTitle = TextView(context)
    private val statusPage = TextView(context)
    private val topBar = LinearLayout(context)
    private val topBack = TextView(context)
    private val topTitle = TextView(context)
    private val topSearch = TextView(context)
    private val topSettings = TextView(context)
    private val bottomBar = LinearLayout(context)
    private val prevBtn = TextView(context)
    private val nextBtn = TextView(context)
    private val seek = SeekBar(context)
    private val seekLabel = TextView(context)
    private val settingsPanel = LinearLayout(context)
    private val fontLabel = TextView(context)
    private val lineLabel = TextView(context)
    private val themeRow = LinearLayout(context)
    private val faceRow = LinearLayout(context)
    private val loadingPill = LinearLayout(context)
    private val loadingText = TextView(context)

    init {
        visibility = GONE
        isClickable = true
        isFocusable = true
        buildCatalogPane()
        buildReadingPane()
        addView(catalogPane, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT))
        addView(readingPane, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT))
        applyTheme()
    }

    // ---------------------------------------------------------------- public API

    fun isActive(): Boolean = mode != Mode.HIDDEN

    fun isReading(): Boolean = mode == Mode.READING

    /** Open the catalog and ask the server for it. */
    fun openCatalog() {
        showMode(Mode.CATALOG)
        requestCatalog()
    }

    fun hide() {
        if (mode == Mode.HIDDEN) return
        showMode(Mode.HIDDEN)
    }

    /** Disconnect: forget everything server-side state depended on. */
    fun reset() {
        hide()
        books = emptyList()
        catalogNotes = emptyList()
        page = null
        currentBook = null
        setLoading(false)
        dict.cancel()
        main.removeCallbacks(dictFinish)
        main.removeCallbacks(dictTimeout)
        dictDialog?.dismiss()
        renderCatalog()
    }

    /** @return true when consumed. */
    fun handleBack(): Boolean = when {
        mode == Mode.READING && settingsVisible -> {
            setSettingsVisible(false)
            true
        }
        mode == Mode.READING -> {
            leaveBook()
            true
        }
        mode == Mode.CATALOG -> {
            hide()
            true
        }
        else -> false
    }

    /** Volume-key / tap-zone forward: scroll a screen, then next page. */
    fun stepForward() {
        if (mode != Mode.READING) return
        if (bodyScroll.canScrollVertically(1)) {
            bodyScroll.smoothScrollBy(0, screenStep())
        } else {
            turnPage(+1)
        }
    }

    fun stepBackward() {
        if (mode != Mode.READING) return
        if (bodyScroll.canScrollVertically(-1)) {
            bodyScroll.smoothScrollBy(0, -screenStep())
        } else {
            turnPage(-1, landAtEnd = true)
        }
    }

    fun handle(events: List<LibraryEvent>) {
        for (ev in events) handleEvent(ev)
    }

    /** @return true when the star body is the reply to a word looked up in the reader. */
    fun feedDict(body: String): Boolean {
        if (!dict.feed(body)) return false
        main.removeCallbacks(dictFinish)
        main.postDelayed(dictFinish, 700)
        return true
    }

    // ---------------------------------------------------------------- events

    private fun handleEvent(ev: LibraryEvent) {
        when (ev) {
            LibraryEvent.CatalogStart -> {
                if (mode != Mode.READING) showMode(Mode.CATALOG)
                catalogProgress.visibility = VISIBLE
            }
            is LibraryEvent.Catalog -> {
                books = ev.books
                catalogNotes = ev.notes
                catalogProgress.visibility = GONE
                if (mode == Mode.CATALOG) setLoading(false)
                renderCatalog()
            }
            is LibraryEvent.PageStart -> {
                if (mode != Mode.READING) showMode(Mode.READING)
            }
            is LibraryEvent.Page -> showPage(ev.page)
            is LibraryEvent.SearchResults -> {
                setLoading(false)
                showSearchResults(ev)
            }
            is LibraryEvent.Notice -> if (isActive()) toast(ev.text.trimEnd('。'))
            is LibraryEvent.Loading -> if (isActive()) setLoading(true, ev.text)
            is LibraryEvent.Error -> if (isActive()) {
                setLoading(false)
                catalogProgress.visibility = GONE
                toast(ev.text)
            }
            LibraryEvent.Closed -> Unit
            LibraryEvent.NeedsReopen -> {
                val book = currentBook
                if (isActive() && book != null && !reopenTried) {
                    reopenTried = true
                    send("/library open ${book.openToken}")
                } else if (isActive()) {
                    setLoading(false)
                    toast("请先从书目中打开一本书")
                    if (mode == Mode.READING) leaveBook()
                }
            }
        }
    }

    private fun showPage(p: LibraryPage) {
        val prev = page
        val samePage = prev != null && prev.title == p.title && prev.page == p.page
        page = p
        reopenTried = false
        setLoading(false)
        if (mode != Mode.READING) showMode(Mode.READING)
        currentBook?.let { book ->
            books = books.map { if (it.index == book.index) it.copy(bookmarkPage = p.page) else it }
        }
        val keepScroll = samePage && !landAtEnd
        val oldY = bodyScroll.scrollY
        renderPage()
        val toEnd = landAtEnd
        landAtEnd = false
        bodyScroll.post {
            when {
                keepScroll -> bodyScroll.scrollTo(0, oldY)
                toEnd -> bodyScroll.scrollTo(0, maxOf(0, bodyColumn.height - bodyScroll.height))
                else -> bodyScroll.scrollTo(0, 0)
            }
        }
        if (!samePage) {
            bodyColumn.alpha = 0.35f
            bodyColumn.animate().alpha(1f).setDuration(160).start()
        }
    }

    private fun showSearchResults(ev: LibraryEvent.SearchResults) {
        if (ev.hits.isEmpty()) {
            toast("《${ev.title}》中没有找到「${ev.query}」")
            return
        }
        if (ev.hits.size == 1) return
        val items = ev.hits.map { "第 ${it.page} 页　${it.snippet}" }.toTypedArray()
        AlertDialog.Builder(context)
            .setTitle("「${ev.query}」共 ${ev.hits.size} 处")
            .setItems(items) { _, which ->
                ev.hits.getOrNull(which)?.let { jumpTo(it.page) }
            }
            .setNegativeButton("关闭", null)
            .show()
    }

    // ---------------------------------------------------------------- actions

    private fun send(cmd: String) {
        if (onSend?.invoke(cmd) != true) {
            setLoading(false)
            catalogProgress.visibility = GONE
        }
    }

    private fun requestCatalog() {
        catalogProgress.visibility = VISIBLE
        send("/library")
    }

    private fun openBook(book: LibraryBook) {
        currentBook = book
        reopenTried = false
        page = null
        landAtEnd = false
        setLoading(true, "正在打开《${book.name.substringBeforeLast('.')}》…")
        send("/library open ${book.openToken}")
    }

    private fun leaveBook() {
        setSettingsVisible(false)
        send("/library close")
        page = null
        showMode(Mode.CATALOG)
        renderCatalog()
        requestCatalog()
    }

    private fun turnPage(delta: Int, landAtEnd: Boolean = false) {
        val p = page ?: return
        if (requestPending) return
        val target = p.page + delta
        if (target < 1) {
            toast("已是第一页")
            return
        }
        if (target > p.total) {
            toast("已是最后一页")
            return
        }
        this.landAtEnd = landAtEnd
        setLoading(true, if (delta > 0) "下一页…" else "上一页…")
        send(if (delta > 0) "/library next" else "/library prev")
    }

    private fun jumpTo(target: Int) {
        val p = page ?: return
        val t = target.coerceIn(1, p.total)
        if (t == p.page) return
        landAtEnd = false
        setLoading(true, "跳转到第 $t 页…")
        send("/library page $t")
    }

    private fun promptJump() {
        val p = page ?: return
        val input = EditText(context).apply {
            inputType = InputType.TYPE_CLASS_NUMBER
            hint = "1 – ${p.total}"
            setText(p.page.toString())
            selectAll()
        }
        AlertDialog.Builder(context)
            .setTitle("跳转到页码")
            .setView(wrapDialogInput(input))
            .setPositiveButton("跳转") { _, _ ->
                input.text?.toString()?.trim()?.toIntOrNull()?.let { jumpTo(it) }
            }
            .setNegativeButton("取消", null)
            .show()
    }

    private fun promptSearch() {
        val input = EditText(context).apply {
            inputType = InputType.TYPE_CLASS_TEXT
            hint = "关键词"
        }
        AlertDialog.Builder(context)
            .setTitle("书内搜索")
            .setView(wrapDialogInput(input))
            .setPositiveButton("搜索") { _, _ ->
                val q = input.text?.toString()?.trim().orEmpty()
                if (q.isNotEmpty()) {
                    setLoading(true, "搜索「$q」…")
                    send("/library search $q")
                }
            }
            .setNegativeButton("取消", null)
            .show()
    }

    private fun lookUp(selected: String) {
        val word = DictCapture.normalize(selected)
        if (word == null) {
            toast("请选择要查询的词")
            return
        }
        if (word.length > DictCapture.MAX_LEN) {
            toast("选中内容太长（最多 ${DictCapture.MAX_LEN} 字）")
            return
        }
        if (onSend?.invoke(DictCapture.command(word)) != true) return
        dict.begin(word)
        dictWord = word
        main.removeCallbacks(dictFinish)
        main.removeCallbacks(dictTimeout)
        main.postDelayed(dictTimeout, 25_000)
        showDict("查询中…")
    }

    private fun showDict(body: CharSequence) {
        val d = dictDialog
        if (d != null && d.isShowing) {
            d.setTitle(dictWord)
            d.setMessage(body)
            return
        }
        dictDialog = AlertDialog.Builder(context)
            .setTitle(dictWord)
            .setMessage(body)
            .setPositiveButton("关闭", null)
            .show()
    }

    private fun formatDict(lines: List<String>): CharSequence {
        val sb = SpannableStringBuilder()
        for (line in lines) {
            if (sb.isNotEmpty()) sb.append('\n')
            val t = line.trim()
            if (t.startsWith("---")) {
                val start = sb.length
                sb.append(t.removePrefix("---").removeSuffix("---").trim())
                sb.setSpan(StyleSpan(Typeface.BOLD), start, sb.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            } else {
                sb.append(line)
            }
        }
        return sb
    }

    private fun wrapDialogInput(input: EditText): View =
        FrameLayout(context).apply {
            setPadding(dp(20), dp(8), dp(20), 0)
            addView(input)
        }

    private fun setLoading(on: Boolean, text: String = "") {
        requestPending = on
        main.removeCallbacks(requestTimeout)
        if (on) {
            loadingText.text = text.ifBlank { "加载中…" }
            loadingPill.visibility = VISIBLE
            main.postDelayed(requestTimeout, 20_000)
        } else {
            loadingPill.visibility = GONE
        }
    }

    private fun toast(msg: String) {
        Toast.makeText(context, msg, Toast.LENGTH_SHORT).show()
    }

    private fun screenStep(): Int {
        val line = bodyText.lineHeight.coerceAtLeast(1)
        return (bodyScroll.height - line * 2).coerceAtLeast(line)
    }

    // ---------------------------------------------------------------- mode / chrome

    private fun showMode(m: Mode) {
        val wasVisible = mode != Mode.HIDDEN
        mode = m
        visibility = if (m == Mode.HIDDEN) GONE else VISIBLE
        catalogPane.visibility = if (m == Mode.CATALOG) VISIBLE else GONE
        readingPane.visibility = if (m == Mode.READING) VISIBLE else GONE
        keepScreenOn = m == Mode.READING
        if (m == Mode.READING) {
            setChromeVisible(false)
            setSettingsVisible(false)
            renderPage()
        } else {
            hideKeyboard()
        }
        if (m == Mode.HIDDEN) setLoading(false)
        val nowVisible = m != Mode.HIDDEN
        if (nowVisible != wasVisible || nowVisible) notifyChrome()
    }

    private fun notifyChrome() {
        val visible = mode != Mode.HIDDEN
        val bg = if (mode == Mode.READING) theme.bg else theme.chrome
        onChromeChanged?.invoke(visible, bg, theme.light)
    }

    private fun setChromeVisible(on: Boolean) {
        chromeVisible = on
        topBar.visibility = if (on) VISIBLE else GONE
        bottomBar.visibility = if (on) VISIBLE else GONE
        if (!on) setSettingsVisible(false)
    }

    private fun setSettingsVisible(on: Boolean) {
        settingsVisible = on
        settingsPanel.visibility = if (on) VISIBLE else GONE
    }

    private fun hideKeyboard() {
        val imm = context.getSystemService(Context.INPUT_METHOD_SERVICE) as? android.view.inputmethod.InputMethodManager
        imm?.hideSoftInputFromWindow(searchInput.windowToken, 0)
        searchInput.clearFocus()
    }

    // ---------------------------------------------------------------- catalog UI

    private fun buildCatalogPane() {
        catalogPane.orientation = LinearLayout.VERTICAL

        catalogHeader.orientation = LinearLayout.HORIZONTAL
        catalogHeader.gravity = Gravity.CENTER_VERTICAL
        catalogHeader.minimumHeight = dp(52)
        catalogBack.apply {
            text = "‹ 聊天"
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 16f)
            setPadding(dp(14), dp(10), dp(14), dp(10))
            setOnClickListener { hide() }
        }
        catalogTitle.apply {
            text = "图书馆"
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 18f)
            typeface = Typeface.DEFAULT_BOLD
            gravity = Gravity.CENTER
        }
        catalogRefresh.apply {
            text = "刷新"
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
            setPadding(dp(14), dp(10), dp(14), dp(10))
            setOnClickListener { requestCatalog() }
        }
        catalogHeader.addView(catalogBack, LinearLayout.LayoutParams(dp(84), LinearLayout.LayoutParams.WRAP_CONTENT))
        catalogHeader.addView(catalogTitle, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        catalogHeader.addView(catalogRefresh, LinearLayout.LayoutParams(dp(84), LinearLayout.LayoutParams.WRAP_CONTENT).also {
            catalogRefresh.gravity = Gravity.END
        })
        catalogPane.addView(catalogHeader)

        searchInput.apply {
            hint = "搜索书名 / 格式"
            setSingleLine()
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
            setPadding(dp(14), dp(10), dp(14), dp(10))
            addTextChangedListener(object : TextWatcher {
                override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) = Unit
                override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) = Unit
                override fun afterTextChanged(s: Editable?) {
                    query = s?.toString().orEmpty()
                    renderCatalog()
                }
            })
        }
        catalogPane.addView(searchInput, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT).also {
            it.setMargins(dp(14), dp(4), dp(14), dp(6))
        })

        val summaryRow = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(16), 0, dp(16), dp(4))
        }
        catalogSummary.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
        summaryRow.addView(catalogSummary, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        catalogProgress.isIndeterminate = true
        catalogProgress.visibility = GONE
        summaryRow.addView(catalogProgress, LinearLayout.LayoutParams(dp(18), dp(18)))
        catalogPane.addView(summaryRow)

        catalogList.orientation = LinearLayout.VERTICAL
        catalogList.setPadding(dp(12), 0, dp(12), dp(24))
        catalogScroll.addView(catalogList)
        catalogPane.addView(catalogScroll, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f))
    }

    private fun renderCatalog() {
        val t = theme
        catalogList.removeAllViews()
        val terms = query.trim().lowercase().split(Regex("\\s+")).filter { it.isNotEmpty() }
        val shown = if (terms.isEmpty()) books else books.filter { b ->
            val hay = "${b.name} ${b.format} ${b.origin.orEmpty()}".lowercase()
            terms.all { it in hay }
        }
        catalogSummary.text = when {
            books.isEmpty() && catalogNotes.isNotEmpty() -> catalogNotes.joinToString("\n")
            books.isEmpty() -> "正在获取书目…"
            terms.isNotEmpty() -> "找到 ${shown.size} / ${books.size} 本"
            else -> "共 ${books.size} 本 · 点击书名开始阅读，书签自动保存"
        }
        catalogSummary.setTextColor(t.sub)

        val reading = if (terms.isEmpty()) books.filter { it.bookmarkPage != null } else emptyList()
        if (reading.isNotEmpty()) {
            catalogList.addView(sectionLabel("继续阅读"))
            reading.forEach { catalogList.addView(bookRow(it, highlight = true)) }
            catalogList.addView(sectionLabel("全部图书"))
        }
        shown.forEach { catalogList.addView(bookRow(it, highlight = false)) }
        if (books.isNotEmpty() && shown.isEmpty()) {
            catalogList.addView(TextView(context).apply {
                text = "没有匹配的图书"
                setTextColor(t.sub)
                gravity = Gravity.CENTER
                setPadding(0, dp(32), 0, dp(32))
            })
        }
    }

    private fun sectionLabel(label: String): View = TextView(context).apply {
        text = label
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
        typeface = Typeface.DEFAULT_BOLD
        setTextColor(theme.sub)
        setPadding(dp(6), dp(14), dp(6), dp(6))
    }

    private fun bookRow(book: LibraryBook, highlight: Boolean): View {
        val t = theme
        val row = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(12), dp(12), dp(12), dp(12))
            background = rounded(if (highlight) blend(t.chrome, t.accent, 0.08f) else t.chrome, dp(10).toFloat(), blend(t.chrome, t.text, 0.10f))
            isClickable = true
            isFocusable = true
            setOnClickListener { openBook(book) }
        }
        val cover = TextView(context).apply {
            text = book.format.take(4)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 11f)
            typeface = Typeface.DEFAULT_BOLD
            setTextColor(Color.WHITE)
            gravity = Gravity.CENTER
            background = rounded(formatColor(book.format), dp(4).toFloat(), null)
        }
        row.addView(cover, LinearLayout.LayoutParams(dp(40), dp(54)).also { it.marginEnd = dp(12) })

        val col = LinearLayout(context).apply { orientation = LinearLayout.VERTICAL }
        col.addView(TextView(context).apply {
            text = book.name.substringBeforeLast('.', book.name)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 16f)
            setTextColor(t.text)
            maxLines = 2
            ellipsize = android.text.TextUtils.TruncateAt.END
        })
        val meta = buildString {
            append("#${book.index} · ${book.size}")
            if (!book.origin.isNullOrBlank()) append(" · @${book.origin}")
        }
        col.addView(TextView(context).apply {
            text = meta
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
            setTextColor(t.sub)
            setPadding(0, dp(4), 0, 0)
        })
        book.bookmarkPage?.let { p ->
            col.addView(TextView(context).apply {
                text = "读到第 $p 页"
                setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
                setTextColor(t.accent)
                setPadding(0, dp(2), 0, 0)
            })
        }
        row.addView(col, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        row.addView(TextView(context).apply {
            text = "›"
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 22f)
            setTextColor(t.sub)
            setPadding(dp(8), 0, 0, 0)
        })
        row.layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT).also {
            it.topMargin = dp(4)
            it.bottomMargin = dp(4)
        }
        return row
    }

    private fun formatColor(fmt: String): Int = when (fmt.uppercase()) {
        "EPUB" -> 0xFF5C6BC0.toInt()
        "PDF" -> 0xFFC62828.toInt()
        "MD" -> 0xFF00897B.toInt()
        else -> 0xFF6D4C41.toInt()
    }

    // ---------------------------------------------------------------- reading UI

    private fun buildReadingPane() {
        bodyColumn.orientation = LinearLayout.VERTICAL
        bodyColumn.setPadding(dp(22), dp(18), dp(22), dp(40))
        pageHeading.apply {
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
            setPadding(0, 0, 0, dp(14))
        }
        bodyText.apply {
            includeFontPadding = true
            if (Build.VERSION.SDK_INT >= 35) {
                justificationMode = Layout.JUSTIFICATION_MODE_INTER_CHARACTER
            } else {
                justificationMode = Layout.JUSTIFICATION_MODE_INTER_WORD
            }
            breakStrategy = Layout.BREAK_STRATEGY_HIGH_QUALITY
            hyphenationFrequency = Layout.HYPHENATION_FREQUENCY_NORMAL
            setTextIsSelectable(true)
            customSelectionActionModeCallback = object : ActionMode.Callback {
                override fun onCreateActionMode(mode: ActionMode, menu: Menu): Boolean {
                    menu.add(Menu.NONE, MENU_DICT, 0, "查词")
                    return true
                }

                override fun onPrepareActionMode(mode: ActionMode, menu: Menu): Boolean = false

                override fun onActionItemClicked(mode: ActionMode, item: MenuItem): Boolean {
                    if (item.itemId != MENU_DICT) return false
                    val a = minOf(selectionStart, selectionEnd).coerceAtLeast(0)
                    val b = maxOf(selectionStart, selectionEnd).coerceAtLeast(0)
                    val picked = text.subSequence(a, b).toString()
                    mode.finish()
                    lookUp(picked)
                    return true
                }

                override fun onDestroyActionMode(mode: ActionMode) = Unit
            }
        }
        pageEnd.apply {
            gravity = Gravity.CENTER
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
            setPadding(0, dp(28), 0, dp(10))
        }
        nextPageBtn.apply {
            gravity = Gravity.CENTER
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
            setPadding(dp(16), dp(12), dp(16), dp(12))
            isClickable = true
            setOnClickListener { turnPage(+1) }
        }
        bodyColumn.addView(pageHeading)
        bodyColumn.addView(bodyText)
        bodyColumn.addView(pageEnd)
        bodyColumn.addView(nextPageBtn, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT).also {
            it.setMargins(dp(24), 0, dp(24), 0)
        })
        bodyScroll.isVerticalScrollBarEnabled = false
        bodyScroll.addView(bodyColumn)

        // Fed from both the selectable body text and the scroll view, so use raw coordinates.
        val gestures = GestureDetector(context, object : GestureDetector.SimpleOnGestureListener() {
            override fun onDown(e: MotionEvent): Boolean {
                selectionAtDown = bodyText.hasSelection()
                return true
            }

            override fun onSingleTapUp(e: MotionEvent): Boolean {
                if (selectionAtDown) return true
                val loc = IntArray(2)
                bodyScroll.getLocationOnScreen(loc)
                onBodyTap(e.rawX - loc[0])
                return true
            }

            override fun onFling(e1: MotionEvent?, e2: MotionEvent, velocityX: Float, velocityY: Float): Boolean {
                val start = e1 ?: return false
                if (selectionAtDown || bodyText.hasSelection()) return false
                val dx = e2.rawX - start.rawX
                val dy = e2.rawY - start.rawY
                if (abs(dx) > dp(72) && abs(dx) > abs(dy) * 1.6f && abs(velocityX) > 600) {
                    if (dx < 0) turnPage(+1) else turnPage(-1)
                    return true
                }
                return false
            }
        })
        bodyScroll.setOnTouchListener { _, ev ->
            gestures.onTouchEvent(ev)
            false
        }
        bodyText.setOnTouchListener { _, ev ->
            gestures.onTouchEvent(ev)
            false
        }

        statusBar.apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(22), dp(4), dp(22), dp(6))
        }
        statusTitle.apply {
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 11f)
            maxLines = 1
            ellipsize = android.text.TextUtils.TruncateAt.END
        }
        statusPage.setTextSize(TypedValue.COMPLEX_UNIT_SP, 11f)
        statusBar.addView(statusTitle, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        statusBar.addView(statusPage)

        val column = LinearLayout(context).apply { orientation = LinearLayout.VERTICAL }
        column.addView(bodyScroll, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f))
        column.addView(statusBar, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT))
        readingPane.addView(column, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT))

        buildTopBar()
        buildBottomBar()
        buildLoadingPill()
    }

    private fun buildTopBar() {
        topBar.apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            minimumHeight = dp(52)
            elevation = dp(4).toFloat()
            visibility = GONE
            isClickable = true
        }
        topBack.apply {
            text = "‹ 书目"
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 16f)
            setPadding(dp(14), dp(10), dp(10), dp(10))
            setOnClickListener { leaveBook() }
        }
        topTitle.apply {
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
            typeface = Typeface.DEFAULT_BOLD
            maxLines = 1
            ellipsize = android.text.TextUtils.TruncateAt.END
            gravity = Gravity.CENTER
        }
        topSearch.apply {
            text = "搜索"
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
            setPadding(dp(10), dp(10), dp(10), dp(10))
            setOnClickListener { promptSearch() }
        }
        topSettings.apply {
            text = "Aa"
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 16f)
            typeface = Typeface.DEFAULT_BOLD
            setPadding(dp(10), dp(10), dp(16), dp(10))
            setOnClickListener { setSettingsVisible(!settingsVisible) }
        }
        topBar.addView(topBack)
        topBar.addView(topTitle, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        topBar.addView(topSearch)
        topBar.addView(topSettings)
        readingPane.addView(topBar, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT, Gravity.TOP))
    }

    private fun buildBottomBar() {
        bottomBar.apply {
            orientation = LinearLayout.VERTICAL
            elevation = dp(4).toFloat()
            visibility = GONE
            isClickable = true
            setPadding(dp(12), dp(8), dp(12), dp(12))
        }

        settingsPanel.apply {
            orientation = LinearLayout.VERTICAL
            visibility = GONE
            setPadding(dp(4), 0, dp(4), dp(10))
        }
        settingsPanel.addView(settingRow("字号", fontLabel, { bumpFont(-1f) }, { bumpFont(1f) }))
        settingsPanel.addView(settingRow("行距", lineLabel, { bumpLine(-0.15f) }, { bumpLine(0.15f) }))
        themeRow.orientation = LinearLayout.HORIZONTAL
        themeRow.gravity = Gravity.CENTER_VERTICAL
        settingsPanel.addView(themeRow, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT).also { it.topMargin = dp(6) })
        faceRow.orientation = LinearLayout.HORIZONTAL
        faceRow.gravity = Gravity.CENTER_VERTICAL
        settingsPanel.addView(faceRow, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT).also { it.topMargin = dp(8) })
        bottomBar.addView(settingsPanel)

        seekLabel.apply {
            gravity = Gravity.CENTER
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
            setPadding(0, 0, 0, dp(2))
            setOnClickListener { promptJump() }
        }
        bottomBar.addView(seekLabel, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT))

        val navRow = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        prevBtn.apply {
            text = "上一页"
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f)
            setPadding(dp(10), dp(10), dp(10), dp(10))
            setOnClickListener { turnPage(-1) }
        }
        nextBtn.apply {
            text = "下一页"
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f)
            setPadding(dp(10), dp(10), dp(10), dp(10))
            setOnClickListener { turnPage(+1) }
        }
        seek.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(sb: SeekBar?, progress: Int, fromUser: Boolean) {
                if (fromUser) {
                    val total = page?.total ?: return
                    seekLabel.text = "跳到第 ${progress + 1} / $total 页"
                }
            }

            override fun onStartTrackingTouch(sb: SeekBar?) = Unit

            override fun onStopTrackingTouch(sb: SeekBar?) {
                val target = (sb?.progress ?: return) + 1
                if (target != page?.page) jumpTo(target) else updateProgressViews()
            }
        })
        navRow.addView(prevBtn)
        navRow.addView(seek, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        navRow.addView(nextBtn)
        bottomBar.addView(navRow)

        readingPane.addView(bottomBar, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT, Gravity.BOTTOM))
    }

    private fun settingRow(label: String, value: TextView, minus: () -> Unit, plus: () -> Unit): View {
        val row = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        row.addView(TextView(context).apply {
            text = label
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f)
            tag = "settings-text"
        }, LinearLayout.LayoutParams(dp(52), LinearLayout.LayoutParams.WRAP_CONTENT))
        row.addView(chip("A−", false) { minus() })
        value.apply {
            gravity = Gravity.CENTER
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f)
            tag = "settings-text"
        }
        row.addView(value, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        row.addView(chip("A+", false) { plus() })
        return row
    }

    private fun chip(label: String, selected: Boolean, onClick: () -> Unit): TextView {
        val t = theme
        return TextView(context).apply {
            text = label
            gravity = Gravity.CENTER
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f)
            setTextColor(if (selected) t.accent else t.text)
            setPadding(dp(16), dp(8), dp(16), dp(8))
            background = rounded(t.chrome, dp(16).toFloat(), if (selected) t.accent else blend(t.chrome, t.text, 0.2f))
            setOnClickListener { onClick() }
        }
    }

    private fun renderSettingChips() {
        themeRow.removeAllViews()
        themes.forEachIndexed { i, th ->
            val swatch = TextView(context).apply {
                text = th.name
                gravity = Gravity.CENTER
                setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
                setTextColor(th.text)
                setPadding(dp(4), dp(10), dp(4), dp(10))
                background = rounded(th.bg, dp(18).toFloat(), if (i == themeIdx) theme.accent else blend(th.bg, th.text, 0.25f), if (i == themeIdx) dp(2) else dp(1))
                setOnClickListener { setTheme(i) }
            }
            themeRow.addView(swatch, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f).also {
                it.marginStart = dp(4)
                it.marginEnd = dp(4)
            })
        }
        faceRow.removeAllViews()
        faceRow.addView(TextView(context).apply {
            text = "字体"
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f)
            setTextColor(theme.text)
        }, LinearLayout.LayoutParams(dp(52), LinearLayout.LayoutParams.WRAP_CONTENT))
        faceRow.addView(chip("宋体", serif) { setSerif(true) }.apply { typeface = Typeface.SERIF })
        faceRow.addView(View(context), LinearLayout.LayoutParams(dp(10), 1))
        faceRow.addView(chip("黑体", !serif) { setSerif(false) }.apply { typeface = Typeface.SANS_SERIF })
    }

    private fun buildLoadingPill() {
        loadingPill.apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(14), dp(8), dp(16), dp(8))
            background = rounded(0xCC000000.toInt(), dp(18).toFloat(), null)
            visibility = GONE
            elevation = dp(6).toFloat()
        }
        loadingPill.addView(ProgressBar(context).apply { isIndeterminate = true }, LinearLayout.LayoutParams(dp(16), dp(16)).also { it.marginEnd = dp(8) })
        loadingText.apply {
            setTextColor(Color.WHITE)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
        }
        loadingPill.addView(loadingText)
        addView(loadingPill, LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT, Gravity.CENTER_HORIZONTAL or Gravity.TOP).also {
            it.topMargin = dp(64)
        })
    }

    private fun onBodyTap(x: Float) {
        val w = bodyScroll.width.coerceAtLeast(1)
        when {
            chromeVisible -> setChromeVisible(false)
            x < w * 0.3f -> stepBackward()
            x > w * 0.7f -> stepForward()
            else -> setChromeVisible(true)
        }
    }

    private fun renderPage() {
        val p = page
        val t = theme
        if (p == null) {
            pageHeading.text = ""
            bodyText.text = ""
            pageEnd.text = ""
            nextPageBtn.visibility = GONE
            statusTitle.text = currentBook?.name.orEmpty()
            statusPage.text = ""
            topTitle.text = currentBook?.name.orEmpty()
            return
        }
        pageHeading.text = "《${p.title}》"
        bodyText.text = buildBody(p.paragraphs)
        pageEnd.text = if (p.page >= p.total) "— 全书完 —" else "— 第 ${p.page} / ${p.total} 页 —"
        nextPageBtn.visibility = if (p.page < p.total) VISIBLE else GONE
        nextPageBtn.text = "继续阅读第 ${p.page + 1} 页  ›"
        nextPageBtn.background = rounded(blend(t.bg, t.text, 0.06f), dp(22).toFloat(), blend(t.bg, t.text, 0.15f))
        statusTitle.text = p.title
        topTitle.text = p.title
        updateProgressViews()
        applyTextStyle()
    }

    private fun updateProgressViews() {
        val p = page ?: return
        val pct = if (p.total <= 1) 100 else ((p.page - 1) * 100f / (p.total - 1)).roundToInt()
        statusPage.text = "${p.page}/${p.total} · $pct%"
        seekLabel.text = "第 ${p.page} / ${p.total} 页 · $pct%　（点此输入页码）"
        seek.max = (p.total - 1).coerceAtLeast(0)
        seek.progress = p.page - 1
        prevBtn.isEnabled = p.page > 1
        nextBtn.isEnabled = p.page < p.total
        prevBtn.alpha = if (prevBtn.isEnabled) 1f else 0.35f
        nextBtn.alpha = if (nextBtn.isEnabled) 1f else 0.35f
    }

    private fun buildBody(paragraphs: List<String>): CharSequence {
        if (paragraphs.isEmpty()) return "（空白页）"
        val sb = SpannableStringBuilder()
        paragraphs.forEachIndexed { i, para ->
            if (i > 0) {
                sb.append('\n')
                val gapStart = sb.length
                sb.append('\n')
                sb.setSpan(RelativeSizeSpan(0.45f), gapStart, sb.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            }
            val first = para.firstOrNull() ?: ' '
            if (first.code >= 0x2E80 && !isOpeningPunct(first)) sb.append("\u3000\u3000")
            sb.append(para)
        }
        return sb
    }

    private fun isOpeningPunct(c: Char): Boolean = c in "「『“‘（《〈【〔—…"

    private fun applyTextStyle() {
        val t = theme
        bodyText.setTextSize(TypedValue.COMPLEX_UNIT_SP, fontSp)
        bodyText.setLineSpacing(0f, lineMult)
        bodyText.letterSpacing = 0.02f
        bodyText.typeface = if (serif) Typeface.SERIF else Typeface.SANS_SERIF
        bodyText.setTextColor(t.text)
        pageHeading.setTextColor(t.sub)
        pageHeading.typeface = bodyText.typeface
        pageEnd.setTextColor(t.sub)
        nextPageBtn.setTextColor(t.accent)
        fontLabel.text = "${fontSp.roundToInt()}"
        lineLabel.text = String.format("%.2f", lineMult)
    }

    // ---------------------------------------------------------------- settings

    private fun bumpFont(delta: Float) {
        fontSp = (fontSp + delta).coerceIn(13f, 32f)
        prefs.edit().putFloat("font_sp", fontSp).apply()
        reflowKeepingPosition()
    }

    private fun bumpLine(delta: Float) {
        lineMult = ((lineMult + delta) * 100).roundToInt() / 100f
        lineMult = lineMult.coerceIn(1.2f, 2.5f)
        prefs.edit().putFloat("line_mult", lineMult).apply()
        reflowKeepingPosition()
    }

    private fun setSerif(on: Boolean) {
        serif = on
        prefs.edit().putBoolean("serif", on).apply()
        renderSettingChips()
        reflowKeepingPosition()
    }

    private fun setTheme(i: Int) {
        themeIdx = i.coerceIn(0, themes.lastIndex)
        prefs.edit().putInt("theme", themeIdx).apply()
        applyTheme()
        renderCatalog()
        if (mode != Mode.HIDDEN) notifyChrome()
    }

    private fun reflowKeepingPosition() {
        val range = (bodyColumn.height - bodyScroll.height).coerceAtLeast(1)
        val frac = bodyScroll.scrollY.toFloat() / range
        applyTextStyle()
        bodyScroll.post {
            val newRange = (bodyColumn.height - bodyScroll.height).coerceAtLeast(0)
            bodyScroll.scrollTo(0, (frac * newRange).roundToInt())
        }
    }

    private fun applyTheme() {
        val t = theme
        setBackgroundColor(t.bg)
        catalogPane.setBackgroundColor(blend(t.bg, t.text, 0.03f))
        catalogHeader.setBackgroundColor(t.chrome)
        catalogTitle.setTextColor(t.text)
        catalogBack.setTextColor(t.accent)
        catalogRefresh.setTextColor(t.accent)
        searchInput.setTextColor(t.text)
        searchInput.setHintTextColor(t.sub)
        searchInput.background = rounded(t.chrome, dp(20).toFloat(), blend(t.chrome, t.text, 0.15f))

        readingPane.setBackgroundColor(t.bg)
        statusTitle.setTextColor(t.sub)
        statusPage.setTextColor(t.sub)
        topBar.setBackgroundColor(t.chrome)
        bottomBar.setBackgroundColor(t.chrome)
        topBack.setTextColor(t.accent)
        topTitle.setTextColor(t.text)
        topSearch.setTextColor(t.accent)
        topSettings.setTextColor(t.accent)
        prevBtn.setTextColor(t.accent)
        nextBtn.setTextColor(t.accent)
        seekLabel.setTextColor(t.sub)
        rebuildSettingRows()
        renderSettingChips()
        renderPage()
    }

    /** Chip backgrounds bake in theme colors; rebuild the +/- rows on theme change. */
    private fun rebuildSettingRows() {
        if (settingsPanel.childCount < 2) return
        (fontLabel.parent as? ViewGroup)?.removeView(fontLabel)
        (lineLabel.parent as? ViewGroup)?.removeView(lineLabel)
        settingsPanel.removeViewAt(1)
        settingsPanel.removeViewAt(0)
        settingsPanel.addView(settingRow("行距", lineLabel, { bumpLine(-0.15f) }, { bumpLine(0.15f) }), 0)
        settingsPanel.addView(settingRow("字号", fontLabel, { bumpFont(-1f) }, { bumpFont(1f) }), 0)
        for (row in 0..1) {
            val v = settingsPanel.getChildAt(row) as? ViewGroup ?: continue
            for (c in 0 until v.childCount) {
                val child = v.getChildAt(c)
                if (child is TextView && child.tag == "settings-text") child.setTextColor(theme.text)
            }
        }
    }

    // ---------------------------------------------------------------- helpers

    private fun rounded(fill: Int, radius: Float, stroke: Int?, strokeWidth: Int = dp(1)): GradientDrawable =
        GradientDrawable().apply {
            setColor(fill)
            cornerRadius = radius
            if (stroke != null) setStroke(strokeWidth, stroke)
        }

    private fun blend(a: Int, b: Int, f: Float): Int {
        val inv = 1f - f
        return Color.rgb(
            (Color.red(a) * inv + Color.red(b) * f).roundToInt(),
            (Color.green(a) * inv + Color.green(b) * f).roundToInt(),
            (Color.blue(a) * inv + Color.blue(b) * f).roundToInt(),
        )
    }

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).roundToInt()

    private companion object {
        const val MENU_DICT = 0x5D1C
    }
}
