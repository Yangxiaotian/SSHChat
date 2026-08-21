package chat.ssh.sshchat

import android.Manifest
import android.content.ClipData
import android.content.ClipboardManager
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Bundle
import android.text.Editable
import android.text.TextWatcher
import android.util.TypedValue
import android.view.View
import android.widget.Button
import android.widget.ScrollView
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
    private var chatSp = 11f
    /** Local file waiting for gui-open upload after /sendfile. */
    @Volatile private var pendingUpload: File? = null
    private var cameraTarget: File? = null

    private val requestCameraPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (granted) launchCamera()
        else Toast.makeText(this, "需要相机权限才能拍照发送", Toast.LENGTH_SHORT).show()
    }

    private val takePicture = registerForActivityResult(
        ActivityResultContracts.TakePicture(),
    ) { ok ->
        val file = cameraTarget
        cameraTarget = null
        if (ok && file != null && file.isFile && file.length() > 0L) {
            beginSendFile(file)
        } else {
            file?.delete()
            Toast.makeText(this, "未拍到照片", Toast.LENGTH_SHORT).show()
        }
    }

    private val pickImage = registerForActivityResult(
        ActivityResultContracts.GetContent(),
    ) { uri: Uri? ->
        if (uri == null) return@registerForActivityResult
        bg.execute {
            try {
                val dir = File(cacheDir, "sshchat-media").also { it.mkdirs() }
                val out = File(dir, "pick-${System.currentTimeMillis()}.jpg")
                contentResolver.openInputStream(uri)?.use { input ->
                    out.outputStream().use { input.copyTo(it) }
                } ?: error("无法读取图片")
                runOnUiThread { beginSendFile(out) }
            } catch (e: Exception) {
                runOnUiThread {
                    Toast.makeText(this, "读图失败: ${e.message}", Toast.LENGTH_SHORT).show()
                }
            }
        }
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
        binding.btnTab.setOnClickListener { applyTabComplete() }
        binding.btnFontMinus.setOnClickListener { bumpFont(-1f) }
        binding.btnFontPlus.setOnClickListener { bumpFont(1f) }
        binding.etDraft.setOnEditorActionListener { _, _, _ ->
            send()
            true
        }
        binding.etDraft.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {}
            override fun afterTextChanged(s: Editable?) {
                refreshSuggestions(s?.toString().orEmpty())
            }
        })
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
            .setItems(arrayOf("拍照发送", "从相册选择")) { _, which ->
                when (which) {
                    0 -> ensureCameraThenShoot()
                    1 -> pickImage.launch("image/*")
                }
            }
            .show()
    }

    private fun ensureCameraThenShoot() {
        val ok = ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) ==
            PackageManager.PERMISSION_GRANTED
        if (ok) launchCamera()
        else requestCameraPermission.launch(Manifest.permission.CAMERA)
    }

    private fun launchCamera() {
        val dir = File(cacheDir, "sshchat-camera").also { it.mkdirs() }
        val file = File(dir, "cap-${System.currentTimeMillis()}.jpg")
        cameraTarget = file
        val uri = FileProvider.getUriForFile(this, "$packageName.files", file)
        takePicture.launch(uri)
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

    private fun bumpFont(delta: Float) {
        chatSp = (chatSp + delta).coerceIn(7f, 22f)
        applyFont()
    }

    private fun applyFont() {
        binding.tvChat.setTextSize(TypedValue.COMPLEX_UNIT_SP, chatSp)
    }

    private fun connect() {
        val user = binding.etUsername.text?.toString()?.trim().orEmpty()
        if (user.isEmpty()) {
            Toast.makeText(this, "请填写 Linux 用户名", Toast.LENGTH_SHORT).show()
            return
        }
        disconnect()
        binding.tvChat.text = ""
        appendLine("[*] connecting…")
        val kp = DeviceKeyStore.toKeyPair(keys.privateSeed)
        val c = SshChatClient(
            host = BuildConfig.DEFAULT_HOST,
            port = BuildConfig.DEFAULT_PORT,
            username = user,
            keyPair = kp,
            onLine = { line -> runOnUiThread { handleIncoming(line) } },
            onStatus = { s -> runOnUiThread { binding.tvStatus.text = s } },
            onDisconnected = { reason ->
                runOnUiThread {
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

    private fun disconnect() {
        pendingUpload = null
        client?.disconnect()
        client = null
        setConnected(false)
        binding.tvStatus.text = "未连接"
    }

    private fun send() {
        val text = binding.etDraft.text?.toString().orEmpty()
        if (text.isBlank()) return
        binding.etDraft.setText("")
        appendLine("› $text")
        client?.send(text)
    }

    private fun handleIncoming(line: String) {
        val open = SecureInvite.parseGuiOpen(line)
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
        if (pendingUpload != null && line.contains("sendfile", ignoreCase = true) &&
            (line.contains("失败") || line.contains("fail", ignoreCase = true) || line.contains("错误"))
        ) {
            pendingUpload = null
            appendLine(line)
            binding.tvStatus.text = "发文件失败"
            return
        }
        if (SecureInvite.isInviteNoise(line)) return
        appendLine(line)
    }

    private fun startUpload(url: String, key: String, file: File) {
        binding.tvStatus.text = "上传中: ${file.name}"
        appendLine("[*] 上传中: ${file.name}")
        bg.execute {
            try {
                val remote = SecureUpload.upload(url, key, file)
                runOnUiThread {
                    binding.tvStatus.text = "已上传: $remote"
                    appendLine("[*] 已上传: $remote")
                    startActivity(
                        MediaPreviewActivity.intent(
                            this,
                            DownloadedMedia(file, remote, "image/jpeg"),
                        ),
                    )
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
        appendLine("[*] 正在接收文件…")
        bg.execute {
            try {
                val dir = File(cacheDir, "sshchat-media")
                val media = SecureDownload.fetch(url, key, dir)
                runOnUiThread {
                    binding.tvStatus.text = "已接收: ${media.name}"
                    appendLine("[*] 已接收: ${media.name}")
                    startActivity(MediaPreviewActivity.intent(this, media))
                }
            } catch (e: Exception) {
                runOnUiThread {
                    binding.tvStatus.text = "收文件失败"
                    appendLine("[*] 收文件失败: ${e.message}")
                }
            }
        }
    }

    private fun appendLine(line: String) {
        val cur = binding.tvChat.text?.toString().orEmpty()
        binding.tvChat.text = if (cur.isEmpty()) line else "$cur\n$line"
        binding.scrollChat.post {
            binding.scrollChat.fullScroll(ScrollView.FOCUS_DOWN)
        }
    }

    private fun setConnected(on: Boolean) {
        binding.btnConnect.isEnabled = !on
        binding.btnDisconnect.isEnabled = on
        binding.etDraft.isEnabled = on
        binding.btnSend.isEnabled = on
        binding.btnPhoto.isEnabled = on
        binding.btnTab.isEnabled = on
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
