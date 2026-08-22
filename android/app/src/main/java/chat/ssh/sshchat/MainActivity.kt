package chat.ssh.sshchat

import android.Manifest
import android.content.ClipData
import android.content.ClipboardManager
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Bundle
import android.text.Editable
import android.text.TextWatcher
import android.graphics.BitmapFactory
import android.graphics.Typeface
import android.util.TypedValue
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import chat.ssh.sshchat.databinding.ActivityMainBinding
import java.io.File
import java.util.concurrent.Executors

class MainActivity : AppCompatActivity() {
    private lateinit var binding: ActivityMainBinding
    private lateinit var keys: DeviceKeyStore.Keys
    private var client: SshChatClient? = null
    private val bg = Executors.newSingleThreadExecutor()
    private var chatSp = 13f
    /** Local file waiting for gui-open upload after /sendfile. */
    @Volatile private var pendingUpload: File? = null
    private var cameraTarget: File? = null
    private var videoTarget: File? = null
    private var voiceRecorder: VoiceRecorder? = null
    /** Camera/gallery/video is open — SSH may drop; reconnect on return. */
    @Volatile private var mediaPickerOpen = false
    private var onConnectedOnce: (() -> Unit)? = null

    private val requestNotifyPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { /* keep-alive still works without; notification may be hidden */ }

    private val requestAudioPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (!granted) {
            Toast.makeText(this, "需要麦克风权限才能发语音", Toast.LENGTH_SHORT).show()
        }
    }

    private val requestCameraPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (granted) pendingCameraAction?.invoke()
        else {
            mediaPickerOpen = false
            Toast.makeText(this, "需要相机权限", Toast.LENGTH_SHORT).show()
        }
        pendingCameraAction = null
    }
    private var pendingCameraAction: (() -> Unit)? = null

    private val takePicture = registerForActivityResult(
        ActivityResultContracts.TakePicture(),
    ) { ok ->
        mediaPickerOpen = false
        val file = cameraTarget
        cameraTarget = null
        if (ok && file != null && file.isFile && file.length() > 0L) {
            ensureConnectedThen { beginSendFile(file) }
        } else {
            file?.delete()
            Toast.makeText(this, "未拍到照片", Toast.LENGTH_SHORT).show()
        }
    }

    private val takeVideo = registerForActivityResult(
        ActivityResultContracts.CaptureVideo(),
    ) { ok ->
        mediaPickerOpen = false
        val file = videoTarget
        videoTarget = null
        if (ok && file != null && file.isFile && file.length() > 0L) {
            ensureConnectedThen { beginSendFile(file) }
        } else {
            file?.delete()
            Toast.makeText(this, "未录到视频", Toast.LENGTH_SHORT).show()
        }
    }

    private val pickImage = registerForActivityResult(
        ActivityResultContracts.GetContent(),
    ) { uri: Uri? ->
        mediaPickerOpen = false
        if (uri == null) return@registerForActivityResult
        copyUriAndSend(uri, "pick-${System.currentTimeMillis()}.jpg")
    }

    private val pickFile = registerForActivityResult(
        ActivityResultContracts.OpenDocument(),
    ) { uri: Uri? ->
        mediaPickerOpen = false
        if (uri == null) return@registerForActivityResult
        val name = guessDisplayName(uri) ?: "file-${System.currentTimeMillis()}"
        copyUriAndSend(uri, name)
    }

    private val pickIdentity = registerForActivityResult(
        ActivityResultContracts.OpenDocument(),
    ) { uri: Uri? ->
        if (uri == null) return@registerForActivityResult
        try {
            keys = DeviceKeyStore.importFromUri(this, uri)
            refreshKeyUi()
            Toast.makeText(this, "密钥已恢复，公钥与重装前相同", Toast.LENGTH_LONG).show()
        } catch (e: Exception) {
            Toast.makeText(this, "恢复失败: ${e.message}", Toast.LENGTH_LONG).show()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        keys = DeviceKeyStore.getOrCreate(this)
        binding.tvHost.text = "${BuildConfig.DEFAULT_HOST}:${BuildConfig.DEFAULT_PORT}"
        refreshKeyUi()

        binding.btnCopyPubkey.setOnClickListener {
            val cm = getSystemService(CLIPBOARD_SERVICE) as ClipboardManager
            cm.setPrimaryClip(ClipData.newPlainText("sshchat-pubkey", keys.publicOpenSshLine))
            Toast.makeText(this, "公钥已复制", Toast.LENGTH_SHORT).show()
        }
        binding.btnRestoreKey.setOnClickListener {
            pickIdentity.launch(arrayOf("*/*", "text/*", "application/octet-stream"))
        }

        binding.btnConnect.setOnClickListener { connect() }
        binding.btnDisconnect.setOnClickListener { disconnect() }
        binding.btnSend.setOnClickListener { send() }
        binding.btnPhoto.setOnClickListener { showPhotoMenu() }
        binding.btnPhoto.setOnLongClickListener {
            ensureCameraThen { launchVideo() }
            true
        }
        binding.btnFile.setOnClickListener { pickAndSendFile() }
        binding.btnCanvas.setOnClickListener { startCanvas() }
        binding.btnClear.setOnClickListener { clearScreen(announce = true) }
        binding.btnSlash.setOnClickListener { insertSlash() }
        binding.btnTab.setOnClickListener { applyTabComplete() }
        setupVoiceButton()
        binding.btnFontMinus.setOnClickListener { bumpFont(-1f) }
        binding.btnFontPlus.setOnClickListener { bumpFont(1f) }
        binding.etDraft.setOnEditorActionListener { _, actionId, event ->
            val fromIme = actionId == android.view.inputmethod.EditorInfo.IME_ACTION_SEND ||
                actionId == android.view.inputmethod.EditorInfo.IME_ACTION_DONE
            val fromEnter = event != null &&
                event.keyCode == android.view.KeyEvent.KEYCODE_ENTER &&
                event.action == android.view.KeyEvent.ACTION_DOWN
            if (fromIme || fromEnter) {
                send()
                true
            } else {
                false
            }
        }
        binding.etDraft.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {}
            override fun afterTextChanged(s: Editable?) {
                refreshSuggestions(s?.toString().orEmpty())
            }
        })
        chatSp = uiPrefs().getFloat(PREF_CHAT_FONT_SP, 13f).coerceIn(7f, 22f)
        applyFont()
        setConnected(false)
    }

    private fun refreshSuggestions(text: String) {
        val items = if (text.startsWith("/")) CommandCompletions.completions(text).take(12) else emptyList()
        val row = binding.suggestRow
        row.removeAllViews()
        if (items.isEmpty()) {
            binding.suggestScroll.visibility = View.GONE
            return
        }
        binding.suggestScroll.visibility = View.VISIBLE
        val pad = (6 * resources.displayMetrics.density).toInt()
        for (item in items) {
            val b = Button(this).apply {
                this.text = item
                textSize = 11f
                isAllCaps = false
                setPadding(pad, pad / 2, pad, pad / 2)
                setOnClickListener { applySuggestion(item) }
            }
            row.addView(b)
        }
    }

    private fun applySuggestion(chosen: String) {
        val fill = if (chosen.endsWith(" ")) chosen else "$chosen "
        binding.etDraft.setText(fill)
        binding.etDraft.setSelection(fill.length)
        refreshSuggestions(fill)
    }

    private fun insertSlash() {
        val et = binding.etDraft
        val text = et.text?.toString().orEmpty()
        val start = et.selectionStart.coerceIn(0, text.length)
        val end = et.selectionEnd.coerceIn(0, text.length)
        val next = text.substring(0, start) + "/" + text.substring(end)
        et.setText(next)
        et.setSelection(start + 1)
        et.requestFocus()
        refreshSuggestions(next)
    }

    private fun applyTabComplete() {
        val text = binding.etDraft.text?.toString().orEmpty()
        val next = CommandCompletions.applyTab(text)
        if (next != null) {
            binding.etDraft.setText(next)
            binding.etDraft.setSelection(next.length)
        }
        refreshSuggestions(binding.etDraft.text?.toString().orEmpty())
    }

    private fun refreshKeyUi() {
        binding.tvPubkey.text = keys.publicOpenSshLine
        binding.tvKeyHint.text = when {
            keys.freshlyGenerated ->
                "已生成新密钥，并备份到「下载/SSHChat/$DURABLE」。重装后一般会自动恢复；若变成新钥，点「从备份文件恢复密钥」。"
            keys.restoredFromBackup ->
                "已恢复上次密钥，公钥不变，通常无需重新登记。"
            else ->
                "本机密钥已存在；备份：下载/$DURABLE。重装可自动恢复，或点下方手动恢复。"
        }
    }

    private companion object {
        const val DURABLE = "SSHChat/${DeviceKeyStore.DURABLE_NAME}"
        const val PREFS_UI = "sshchat_ui"
        const val PREF_CHAT_FONT_SP = "chat_font_sp"
    }

    private fun setupVoiceButton() {
        binding.btnVoice.setOnTouchListener { v, ev ->
            when (ev.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    if (client == null) {
                        Toast.makeText(this, "请先连接", Toast.LENGTH_SHORT).show()
                        return@setOnTouchListener true
                    }
                    if (pendingUpload != null) {
                        Toast.makeText(this, "已有文件正在上传，请稍候", Toast.LENGTH_SHORT).show()
                        return@setOnTouchListener true
                    }
                    if (!hasAudioPermission()) {
                        requestAudioPermission.launch(Manifest.permission.RECORD_AUDIO)
                        return@setOnTouchListener true
                    }
                    startVoiceRecord()
                    v.parent?.requestDisallowInterceptTouchEvent(true)
                    true
                }
                MotionEvent.ACTION_UP -> {
                    finishVoiceRecord(send = true)
                    true
                }
                MotionEvent.ACTION_CANCEL -> {
                    finishVoiceRecord(send = false)
                    true
                }
                else -> false
            }
        }
    }

    private fun hasAudioPermission(): Boolean =
        ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED

    private fun startVoiceRecord() {
        try {
            val dir = File(cacheDir, "sshchat-voice").also { it.mkdirs() }
            val rec = VoiceRecorder(this, dir)
            rec.start()
            voiceRecorder = rec
            binding.btnVoice.setColorFilter(0xFFC62828.toInt())
            binding.btnVoice.contentDescription = "松开发送"
            binding.tvStatus.text = "正在录音…"
            binding.tvMediaHint.text = "松开手指发送"
        } catch (e: Exception) {
            voiceRecorder = null
            Toast.makeText(this, "无法录音: ${e.message}", Toast.LENGTH_SHORT).show()
        }
    }

    private fun finishVoiceRecord(send: Boolean) {
        val rec = voiceRecorder ?: return
        voiceRecorder = null
        binding.btnVoice.clearColorFilter()
        binding.btnVoice.contentDescription = "按住说话"
        binding.tvMediaHint.text = "话筒语音 · 相机拍照/长按录像 · 文件夹 · 画板 · 垃圾桶清屏"
        if (!send) {
            rec.cancel()
            binding.tvStatus.text = "已取消录音"
            return
        }
        val file = rec.stop(minMs = 400L)
        if (file == null) {
            binding.tvStatus.text = "录音太短"
            Toast.makeText(this, "录音太短", Toast.LENGTH_SHORT).show()
            return
        }
        ensureConnectedThen { beginSendFile(file) }
    }

    private fun pickAndSendFile() {
        if (client == null) {
            Toast.makeText(this, "请先连接", Toast.LENGTH_SHORT).show()
            return
        }
        if (pendingUpload != null) {
            Toast.makeText(this, "已有文件正在上传，请稍候", Toast.LENGTH_SHORT).show()
            return
        }
        mediaPickerOpen = true
        pickFile.launch(arrayOf("*/*"))
    }

    private fun startCanvas() {
        if (client == null) {
            Toast.makeText(this, "请先连接", Toast.LENGTH_SHORT).show()
            return
        }
        appendLine("[*] 正在创建共享画板…（/canvas）")
        client?.send("/canvas")
    }

    private fun copyUriAndSend(uri: Uri, fallbackName: String) {
        bg.execute {
            try {
                val dir = File(cacheDir, "sshchat-media").also { it.mkdirs() }
                val safe = fallbackName.replace(Regex("""[\\/]"""), "_").take(180)
                val out = File(dir, "${System.currentTimeMillis()}-$safe")
                contentResolver.openInputStream(uri)?.use { input ->
                    out.outputStream().use { input.copyTo(it) }
                } ?: error("无法读取文件")
                runOnUiThread { ensureConnectedThen { beginSendFile(out) } }
            } catch (e: Exception) {
                runOnUiThread {
                    Toast.makeText(this, "读取失败: ${e.message}", Toast.LENGTH_SHORT).show()
                }
            }
        }
    }

    private fun guessDisplayName(uri: Uri): String? {
        contentResolver.query(uri, arrayOf(android.provider.OpenableColumns.DISPLAY_NAME), null, null, null)
            ?.use { c ->
                if (c.moveToFirst()) {
                    val i = c.getColumnIndex(android.provider.OpenableColumns.DISPLAY_NAME)
                    if (i >= 0) return c.getString(i)?.takeIf { it.isNotBlank() }
                }
            }
        return uri.lastPathSegment?.substringAfterLast('/')
    }

    private fun showPhotoMenu() {
        if (client == null) {
            Toast.makeText(this, "请先连接", Toast.LENGTH_SHORT).show()
            return
        }
        if (pendingUpload != null) {
            Toast.makeText(this, "已有文件正在上传，请稍候", Toast.LENGTH_SHORT).show()
            return
        }
        AlertDialog.Builder(this)
            .setTitle("发图")
            .setItems(arrayOf("拍照发送", "从相册选择", "录像发送")) { _, which ->
                when (which) {
                    0 -> ensureCameraThen { launchCamera() }
                    1 -> {
                        mediaPickerOpen = true
                        pickImage.launch("image/*")
                    }
                    2 -> ensureCameraThen { launchVideo() }
                }
            }
            .show()
    }

    private fun ensureCameraThen(action: () -> Unit) {
        val ok = ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) ==
            PackageManager.PERMISSION_GRANTED
        if (ok) action()
        else {
            pendingCameraAction = action
            requestCameraPermission.launch(Manifest.permission.CAMERA)
        }
    }

    private fun launchCamera() {
        mediaPickerOpen = true
        val dir = File(cacheDir, "sshchat-camera").also { it.mkdirs() }
        val file = File(dir, "cap-${System.currentTimeMillis()}.jpg")
        cameraTarget = file
        val uri = FileProvider.getUriForFile(this, "$packageName.files", file)
        takePicture.launch(uri)
    }

    private fun launchVideo() {
        if (client == null) {
            Toast.makeText(this, "请先连接", Toast.LENGTH_SHORT).show()
            return
        }
        if (pendingUpload != null) {
            Toast.makeText(this, "已有文件正在上传，请稍候", Toast.LENGTH_SHORT).show()
            return
        }
        mediaPickerOpen = true
        val dir = File(cacheDir, "sshchat-camera").also { it.mkdirs() }
        val file = File(dir, "vid-${System.currentTimeMillis()}.mp4")
        videoTarget = file
        val uri = FileProvider.getUriForFile(this, "$packageName.files", file)
        takeVideo.launch(uri)
    }

    private fun beginSendFile(file: File) {
        if (client == null) {
            Toast.makeText(this, "请先连接", Toast.LENGTH_SHORT).show()
            return
        }
        pendingUpload = file
        binding.tvStatus.text = "等待上传通道…"
        appendLine("[*] 正在发文件: ${file.name}（/sendfile）")
        client?.send("/sendfile")
    }

    /** Reconnect if camera/gallery killed the SSH socket, then run [block]. */
    private fun ensureConnectedThen(block: () -> Unit) {
        if (client != null) {
            block()
            return
        }
        val user = binding.etUsername.text?.toString()?.trim().orEmpty().ifEmpty {
            getSharedPreferences("sshchat_ui", MODE_PRIVATE).getString("username", "").orEmpty()
        }
        if (user.isEmpty()) {
            Toast.makeText(this, "连接已断，请重新登录", Toast.LENGTH_SHORT).show()
            setConnected(false)
            return
        }
        if (binding.etUsername.text.isNullOrEmpty()) {
            binding.etUsername.setText(user)
        }
        appendLine("[*] 拍照/录像/选图期间连接中断，正在重连…")
        onConnectedOnce = block
        connect(clearChat = false)
    }

    private fun bumpFont(delta: Float) {
        chatSp = (chatSp + delta).coerceIn(7f, 22f)
        uiPrefs().edit().putFloat(PREF_CHAT_FONT_SP, chatSp).apply()
        applyFont()
    }

    private fun uiPrefs() = getSharedPreferences(PREFS_UI, MODE_PRIVATE)

    private fun applyFont() {
        for (i in 0 until binding.chatLog.childCount) {
            when (val child = binding.chatLog.getChildAt(i)) {
                is TextView -> child.setTextSize(TypedValue.COMPLEX_UNIT_SP, chatSp)
                is ViewGroup -> applyFontToGroup(child)
            }
        }
    }

    private fun applyFontToGroup(group: ViewGroup) {
        for (i in 0 until group.childCount) {
            when (val child = group.getChildAt(i)) {
                is TextView -> if (child !is Button) {
                    child.setTextSize(TypedValue.COMPLEX_UNIT_SP, chatSp)
                }
                is ViewGroup -> applyFontToGroup(child)
            }
        }
    }

    private fun connect(clearChat: Boolean = true) {
        val user = binding.etUsername.text?.toString()?.trim().orEmpty()
        if (user.isEmpty()) {
            Toast.makeText(this, "请填写 Linux 用户名", Toast.LENGTH_SHORT).show()
            onConnectedOnce = null
            return
        }
        maybeAskNotifyPermission()
        client?.disconnect()
        client = null
        if (clearChat) {
            binding.chatLog.removeAllViews()
            appendLine("[*] connecting…")
        }
        val kp = DeviceKeyStore.toKeyPair(keys.privateSeed)
        val c = SshChatClient(
            host = BuildConfig.DEFAULT_HOST,
            port = BuildConfig.DEFAULT_PORT,
            username = user,
            keyPair = kp,
            onLine = { line -> runOnUiThread { handleIncoming(line) } },
            onStatus = { s ->
                runOnUiThread {
                    binding.tvStatus.text = s
                    if (s.startsWith("已连接")) {
                        SshKeepAliveService.start(this)
                        val once = onConnectedOnce
                        onConnectedOnce = null
                        once?.invoke()
                    }
                }
            },
            onDisconnected = { reason ->
                runOnUiThread {
                    client = null
                    SshKeepAliveService.stop(this)
                    if (mediaPickerOpen) {
                        binding.tvStatus.text = "拍照/选图中（返回后自动重连）…"
                        return@runOnUiThread
                    }
                    setConnected(false)
                    binding.tvStatus.text = reason?.let { "断开：$it" } ?: "已断开"
                }
            },
        )
        client = c
        setConnected(true)
        c.connect()
        getSharedPreferences("sshchat_ui", MODE_PRIVATE)
            .edit()
            .putString("username", user)
            .apply()
    }

    private fun maybeAskNotifyPermission() {
        if (android.os.Build.VERSION.SDK_INT < 33) return
        val ok = ContextCompat.checkSelfPermission(
            this,
            Manifest.permission.POST_NOTIFICATIONS,
        ) == PackageManager.PERMISSION_GRANTED
        if (!ok) requestNotifyPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
    }

    private fun disconnect() {
        mediaPickerOpen = false
        onConnectedOnce = null
        pendingUpload = null
        voiceRecorder?.cancel()
        voiceRecorder = null
        binding.btnVoice.clearColorFilter()
        binding.btnVoice.contentDescription = "按住说话"
        SshKeepAliveService.stop(this)
        client?.disconnect()
        client = null
        setConnected(false)
        binding.tvStatus.text = "未连接"
    }

    private var lastSendAt = 0L
    private var lastSendText = ""

    private fun send() {
        val text = binding.etDraft.text?.toString().orEmpty()
        if (text.isBlank()) return
        val now = android.os.SystemClock.elapsedRealtime()
        // Soft keyboard + Send button (or repeated IME_ACTION) can fire twice quickly.
        if (text == lastSendText && now - lastSendAt < 500L) return
        lastSendText = text
        lastSendAt = now
        binding.etDraft.setText("")
        val cmd = text.trim().lowercase()
        if (cmd == "/cls" || cmd == "/clear") {
            // Clear locally like PC/terminal clients; still notify server for ack.
            clearScreen(announce = false)
            client?.send(text.trim())
            return
        }
        // Don't local-echo: server broadcast is the source of truth (same as desktop GUI).
        client?.send(text)
    }

    private fun clearScreen(announce: Boolean) {
        binding.chatLog.removeAllViews()
        if (announce) {
            appendLine("[*] Screen cleared.")
        }
    }

    private val screenCleared = Regex(
        """^(?:\[[\d:.\sAPMapm/-]+]\s*)?(?:\[\*]\s*)?Screen cleared\.?\s*$""",
        RegexOption.IGNORE_CASE,
    )
    private val ansiClear = Regex("""\u001B\[[0-9;]*[HJKjk]""")

    private fun handleIncoming(line: String) {
        if (PtyNoise.shouldDrop(line)) return
        val stripped = ansiClear.replace(line, "").trim()
        if (stripped.isEmpty()) return
        if (screenCleared.matches(stripped) ||
            stripped.equals("Screen cleared.", ignoreCase = true) ||
            stripped.equals("[*] Screen cleared.", ignoreCase = true)
        ) {
            clearScreen(announce = false)
            appendLine("[*] Screen cleared.")
            return
        }
        val open = SecureInvite.parseGuiOpen(stripped)
        if (open != null) {
            when (open.kind) {
                SecureInvite.Kind.DOWNLOAD -> startDownload(open.url, open.key)
                SecureInvite.Kind.CANVAS -> {
                    appendLine("[*] 打开共享画布…")
                    startActivity(WebInviteActivity.canvas(this, open.url, open.key))
                }
                SecureInvite.Kind.UPLOAD -> {
                    val pending = pendingUpload
                    if (pending != null) {
                        pendingUpload = null
                        startUpload(open.url, open.key, pending)
                    } else {
                        appendLine("[*] 打开上传页…")
                        startActivity(WebInviteActivity.upload(this, open.url, open.key))
                    }
                }
            }
            return
        }
        if (pendingUpload != null && stripped.contains("sendfile", ignoreCase = true) &&
            (stripped.contains("失败") || stripped.contains("fail", ignoreCase = true) || stripped.contains("错误"))
        ) {
            pendingUpload = null
            appendLine(stripped)
            binding.tvStatus.text = "发文件失败"
            return
        }
        if (SecureInvite.isInviteNoise(stripped)) return
        appendLine(stripped)
    }

    private fun startUpload(url: String, key: String, file: File) {
        binding.tvStatus.text = "上传中: ${file.name}"
        appendLine("[*] 上传中: ${file.name}")
        bg.execute {
            try {
                val remote = SecureUpload.upload(url, key, file)
                val mime = MediaMime.guess(remote.ifBlank { file.name })
                val media = DownloadedMedia(file, remote.ifBlank { file.name }, mime)
                runOnUiThread {
                    binding.tvStatus.text = "已上传: ${media.name}"
                    appendMediaEntry(media, autoOpen = media.isImage || media.isVideo)
                }
            } catch (e: Exception) {
                runOnUiThread {
                    binding.tvStatus.text = "发文件失败"
                    appendLine("[*] 发文件失败（${file.name}）: ${e.message}")
                }
            }
        }
    }

    private fun startDownload(url: String, key: String) {
        binding.tvStatus.text = "正在接收文件…"
        bg.execute {
            try {
                val dir = File(cacheDir, "sshchat-media")
                val media = SecureDownload.fetch(url, key, dir)
                runOnUiThread {
                    binding.tvStatus.text = "已接收: ${media.name}"
                    appendMediaEntry(media, autoOpen = media.isImage || media.isVideo || media.isAudio)
                }
            } catch (e: Exception) {
                runOnUiThread {
                    binding.tvStatus.text = "收文件失败"
                    appendLine("[*] 收文件失败: ${e.message}")
                }
            }
        }
    }

    /** Chat chip + optional thumb; tap anytime to reopen preview (like PC 预览). */
    private fun appendMediaEntry(media: DownloadedMedia, autoOpen: Boolean) {
        val kind = MediaMime.kindLabel(media.mime, media.name)
        val action = when {
            media.isAudio -> "点按播放"
            media.isVideo -> "点按播放"
            media.isImage -> "点按再次查看"
            else -> "点按打开"
        }
        val size = formatSize(media.file.length())
        val card = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(10), dp(8), dp(10), dp(8))
            setBackgroundColor(0xFFE8F5E9.toInt())
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ).also { it.topMargin = dp(4); it.bottomMargin = dp(4) }
            isClickable = true
            isFocusable = true
            setOnClickListener {
                if (!media.file.isFile) {
                    Toast.makeText(this@MainActivity, "本地文件已失效", Toast.LENGTH_SHORT).show()
                    return@setOnClickListener
                }
                startActivity(MediaPreviewActivity.intent(this@MainActivity, media))
            }
        }
        val title = TextView(this).apply {
            text = "[$kind] ${media.name} ($size)"
            setTextSize(TypedValue.COMPLEX_UNIT_SP, chatSp)
            setTextColor(0xFF1B5E20.toInt())
            typeface = Typeface.MONOSPACE
            setTypeface(typeface, Typeface.BOLD)
        }
        val hint = TextView(this).apply {
            text = action
            setTextSize(TypedValue.COMPLEX_UNIT_SP, (chatSp - 1f).coerceAtLeast(10f))
            setTextColor(0xFF0B57D0.toInt())
            paint.isUnderlineText = true
            setPadding(0, dp(2), 0, 0)
        }
        card.addView(title)
        if (media.isImage && media.file.isFile) {
            val thumb = ImageView(this).apply {
                adjustViewBounds = true
                scaleType = ImageView.ScaleType.FIT_START
                maxHeight = dp(160)
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                ).also { it.topMargin = dp(6) }
                contentDescription = media.name
                setImageBitmap(decodeThumb(media.file, dp(320)))
            }
            card.addView(thumb)
        }
        card.addView(hint)
        binding.chatLog.addView(card)
        scrollChatToBottom()
        if (autoOpen) {
            binding.chatLog.postDelayed({
                if (media.file.isFile) {
                    startActivity(MediaPreviewActivity.intent(this, media))
                }
            }, 80L)
        }
    }

    private fun decodeThumb(file: File, maxPx: Int): android.graphics.Bitmap? {
        return try {
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeFile(file.absolutePath, bounds)
            var sample = 1
            val w = bounds.outWidth.coerceAtLeast(1)
            val h = bounds.outHeight.coerceAtLeast(1)
            while (w / sample > maxPx || h / sample > maxPx) sample *= 2
            val opts = BitmapFactory.Options().apply { inSampleSize = sample }
            BitmapFactory.decodeFile(file.absolutePath, opts)
        } catch (_: Exception) {
            null
        }
    }

    private fun appendLine(line: String) {
        val tv = TextView(this).apply {
            text = line
            setTextSize(TypedValue.COMPLEX_UNIT_SP, chatSp)
            setTextColor(0xFF222222.toInt())
            typeface = Typeface.MONOSPACE
            setTextIsSelectable(true)
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            )
        }
        binding.chatLog.addView(tv)
        scrollChatToBottom()
    }

    private fun scrollChatToBottom() {
        binding.scrollChat.post {
            binding.scrollChat.fullScroll(ScrollView.FOCUS_DOWN)
        }
    }

    private fun dp(v: Int): Int =
        (v * resources.displayMetrics.density).toInt()

    private fun formatSize(bytes: Long): String = when {
        bytes < 1024 -> "$bytes B"
        bytes < 1024 * 1024 -> String.format("%.1f KB", bytes / 1024.0)
        else -> String.format("%.1f MB", bytes / (1024.0 * 1024.0))
    }

    private fun setConnected(on: Boolean) {
        binding.btnConnect.isEnabled = !on
        binding.btnDisconnect.isEnabled = on
        binding.etDraft.isEnabled = on
        binding.btnSend.isEnabled = on
        binding.btnPhoto.isEnabled = on
        binding.btnVoice.isEnabled = on
        binding.btnFile.isEnabled = on
        binding.btnCanvas.isEnabled = on
        binding.btnClear.isEnabled = true
        binding.btnSlash.isEnabled = on
        binding.btnTab.isEnabled = on
        val iconAlpha = if (on) 1f else 0.35f
        binding.btnPhoto.alpha = iconAlpha
        binding.btnVoice.alpha = iconAlpha
        binding.btnFile.alpha = iconAlpha
        binding.btnCanvas.alpha = iconAlpha
        binding.btnClear.alpha = 1f
        binding.loginPanel.visibility = if (on) View.GONE else View.VISIBLE
        if (!on) {
            binding.suggestScroll.visibility = View.GONE
            binding.suggestRow.removeAllViews()
            val saved = getSharedPreferences("sshchat_ui", MODE_PRIVATE)
                .getString("username", "")
            if (!saved.isNullOrEmpty() && binding.etUsername.text.isNullOrEmpty()) {
                binding.etUsername.setText(saved)
            }
        }
    }

    override fun onDestroy() {
        disconnect()
        bg.shutdownNow()
        super.onDestroy()
    }
}
