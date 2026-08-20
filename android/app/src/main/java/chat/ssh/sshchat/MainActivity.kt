package chat.ssh.sshchat

import android.content.ClipData
import android.content.ClipboardManager
import android.os.Bundle
import android.view.View
import android.widget.ScrollView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import chat.ssh.sshchat.databinding.ActivityMainBinding

class MainActivity : AppCompatActivity() {
    private lateinit var binding: ActivityMainBinding
    private lateinit var keys: DeviceKeyStore.Keys
    private var client: SshChatClient? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        keys = DeviceKeyStore.getOrCreate(this)
        binding.tvHost.text = "${BuildConfig.DEFAULT_HOST}:${BuildConfig.DEFAULT_PORT}"
        binding.tvPubkey.text = keys.publicOpenSshLine
        if (keys.freshlyGenerated) {
            binding.tvKeyHint.text = "已在本机首次启动时生成密钥（安装后第一次打开）。请把公钥交给管理员登记后再连接。"
        } else {
            binding.tvKeyHint.text = "本机密钥已存在（首次安装打开时生成）。把公钥交给管理员：admin-add-user.sh <用户名> <公钥>"
        }

        binding.btnCopyPubkey.setOnClickListener {
            val cm = getSystemService(CLIPBOARD_SERVICE) as ClipboardManager
            cm.setPrimaryClip(ClipData.newPlainText("sshchat-pubkey", keys.publicOpenSshLine))
            Toast.makeText(this, "公钥已复制", Toast.LENGTH_SHORT).show()
        }

        binding.btnConnect.setOnClickListener { connect() }
        binding.btnDisconnect.setOnClickListener { disconnect() }
        binding.btnSend.setOnClickListener { send() }
        binding.etDraft.setOnEditorActionListener { _, _, _ ->
            send()
            true
        }
        setConnected(false)
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
            onLine = { line -> runOnUiThread { appendLine(line) } },
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
        binding.loginPanel.visibility = if (on) View.GONE else View.VISIBLE
        if (!on) {
            val saved = getSharedPreferences("sshchat_ui", MODE_PRIVATE)
                .getString("username", "")
            if (!saved.isNullOrEmpty() && binding.etUsername.text.isNullOrEmpty()) {
                binding.etUsername.setText(saved)
            }
        }
    }

    override fun onDestroy() {
        disconnect()
        super.onDestroy()
    }
}
