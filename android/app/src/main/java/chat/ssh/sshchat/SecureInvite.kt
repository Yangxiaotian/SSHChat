package chat.ssh.sshchat

/**
 * Parses server `gui-open` lines and filters the multi-line invite noise that
 * GUI clients collapse (same idea as sshchat_gui._is_secure_invite_noise).
 */
object SecureInvite {
    enum class Kind { DOWNLOAD, CANVAS, UPLOAD }

    data class Open(val kind: Kind, val url: String, val key: String)

    private val guiOpen = Regex(
        """^(?:\[[*]\]\s*)?gui-open\s+(download|canvas|upload)\s+(https?://\S+)\s+([A-Z0-9]{6})\s*$""",
        RegexOption.IGNORE_CASE,
    )

    private val bannerStart = Regex(
        """^(=+\s*)?(共享画布|文件上传信息|收到新文件|Shared\s+canvas|File\s+upload|New\s+file)""",
        RegexOption.IGNORE_CASE,
    )
    private val bannerEnd = Regex("""^=+\s*$""")
    private val urlLabel = Regex("""(下载|上传|画布|Download|Upload|Canvas).{0,12}(链接|URL|link)""", RegexOption.IGNORE_CASE)
    private val keyLine = Regex("""^(密钥|Key)\s*[:：]""", RegexOption.IGNORE_CASE)
    private val httpOnly = Regex("""^https?://\S+$""", RegexOption.IGNORE_CASE)
    private val metaLine = Regex(
        """^(房间|Room|发送者|Sender|文件名|Filename|大小|Size|过期|Expires)\s*[:：]""",
        RegexOption.IGNORE_CASE,
    )
    private val instrBullet = Regex(
        """^\d+\.\s+(打开|选择|输入|上传|下载|文件只能|每个接收|图形客户端|Enter|Open|Click|Choose|Select|Upload|Download|Preview|This page|Each recipient|The key|Verify)""",
        RegexOption.IGNORE_CASE,
    )

    fun parseGuiOpen(line: String): Open? {
        val m = guiOpen.matchEntire(line.trim()) ?: return null
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
        val t = line.trim()
        if (t.isEmpty()) return true
        if (parseGuiOpen(t) != null) return true
        if (bannerStart.containsMatchIn(t) || bannerEnd.matches(t)) return true
        if (urlLabel.containsMatchIn(t) || keyLine.containsMatchIn(t)) return true
        if (httpOnly.matches(t)) return true
        if (t.equals("说明:", ignoreCase = true) || t.equals("Instructions:", ignoreCase = true)) return true
        if (t.matches(Regex("""^(说明|Instructions?)\s*:?\s*$""", RegexOption.IGNORE_CASE))) return true
        if (instrBullet.containsMatchIn(t)) return true
        if (metaLine.containsMatchIn(t)) return true
        if (t.startsWith("经联邦节点")) return true
        if ("图形客户端会折叠" in t) return true
        return false
    }
}
