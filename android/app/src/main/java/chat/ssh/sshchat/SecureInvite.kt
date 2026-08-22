package chat.ssh.sshchat

/**
 * Parses server `gui-open` lines and filters the multi-line invite noise that
 * GUI clients collapse (same idea as sshchat_gui._is_secure_invite_noise).
 */
object SecureInvite {
    enum class Kind { DOWNLOAD, CANVAS, UPLOAD }

    data class Open(val kind: Kind, val url: String, val key: String)

    data class FileMeta(
        var sender: String? = null,
        var filename: String? = null,
        var room: String? = null,
    ) {
        fun reset() {
            sender = null
            filename = null
            room = null
        }
    }

    private val guiOpen = Regex(
        """^(?:\[[*]\]\s*)?gui-open\s+(download|canvas|upload)\s+(https?://\S+)\s+([A-Z0-9]{6})\s*$""",
        RegexOption.IGNORE_CASE,
    )

    private val bannerStart = Regex(
        """^(=+\s*)?(共享画布|文件上传信息|收到新文件|Shared\s+canvas|File\s+upload|New\s+file)""",
        RegexOption.IGNORE_CASE,
    )
    private val bannerEnd = Regex("""^=+\s*$""")
    private val urlLabel = Regex(
        """(画布网址|上传网址|下载网址|Canvas\s*URL|Upload\s*URL|Download\s*URL|网址)\s*:?\s*$""",
        RegexOption.IGNORE_CASE,
    )
    private val keyLine = Regex(
        """^(?:访问密钥|上传密钥|下载密钥|Access\s*key|Upload\s*key|Download\s*key|密钥)\s*[:：]\s*[A-Z0-9]{6}\s*$""",
        RegexOption.IGNORE_CASE,
    )
    private val httpOnly = Regex("""^https?://\S+$""", RegexOption.IGNORE_CASE)
    private val metaLine = Regex(
        """^(发起人|发件人|文件名|大小|范围|来自房间|标题|接收者|房间|发送者|""" +
            """From|Sender|Filename|Size|Room|Recipients?)\s*[:：]""",
        RegexOption.IGNORE_CASE,
    )
    private val instrBullet = Regex(
        """^\d+\.\s+(打开|选择|输入|上传|下载|文件只能|每个接收|图形客户端|""" +
            """Enter|Open|Click|Choose|Select|Upload|Download|Preview|""" +
            """This page|Each recipient|The key|Verify)""",
        RegexOption.IGNORE_CASE,
    )
    private val starPrefix = Regex("""^\[\*]\s*""")
    private val timePrefix = Regex(
        """^(?:\[[\d:.\sAPMapm/-]+]\s*)+""",
    )

    /** Collect sender/filename/room from invite lines before gui-open arrives. */
    fun absorbFileMeta(line: String, meta: FileMeta) {
        val t = normalize(line)
        if (bannerStart.containsMatchIn(t)) {
            meta.reset()
            return
        }
        Regex("""^(?:发起人|发件人|From|Sender)\s*[:：]\s*(.+)$""", RegexOption.IGNORE_CASE)
            .matchEntire(t)?.let { meta.sender = it.groupValues[1].trim(); return }
        Regex("""^(?:文件名|Filename)\s*[:：]\s*(.+)$""", RegexOption.IGNORE_CASE)
            .matchEntire(t)?.let { meta.filename = it.groupValues[1].trim(); return }
        Regex("""^(?:范围|来自房间|Room)\s*[:：]\s*(.+)$""", RegexOption.IGNORE_CASE)
            .matchEntire(t)?.let { meta.room = it.groupValues[1].trim(); return }
    }

    fun parseGuiOpen(line: String): Open? {
        val m = guiOpen.matchEntire(normalize(line)) ?: return null
        val kind = when (m.groupValues[1].lowercase()) {
            "download" -> Kind.DOWNLOAD
            "canvas" -> Kind.CANVAS
            "upload" -> Kind.UPLOAD
            else -> return null
        }
        return Open(kind, m.groupValues[2], m.groupValues[3].uppercase())
    }

    /** Hide invite boilerplate; keep normal chat. */
    fun isInviteNoise(line: String): Boolean {
        val t = normalize(line)
        if (t.isEmpty()) return true
        if (parseGuiOpen(t) != null) return true
        if (bannerStart.containsMatchIn(t) || bannerEnd.matches(t)) return true
        if (urlLabel.containsMatchIn(t) || keyLine.matches(t)) return true
        if (httpOnly.matches(t)) return true
        if (t.matches(Regex("""^(说明|Instructions?)\s*:?\s*$""", RegexOption.IGNORE_CASE))) {
            return true
        }
        if (instrBullet.containsMatchIn(t)) return true
        if (metaLine.containsMatchIn(t)) return true
        if (t.startsWith("经联邦节点")) return true
        if ("图形客户端会折叠" in t) return true
        if (t.contains("只能下载一次") || t.contains("存好之前别关")) return true
        if (t.contains("网址和密钥都不同")) return true
        if (t.contains("此网址随后作废")) return true
        return false
    }

    private fun normalize(raw: String): String {
        var s = raw.trim()
        s = timePrefix.replaceFirst(s, "").trim()
        s = starPrefix.replaceFirst(s, "").trim()
        return s
    }
}
