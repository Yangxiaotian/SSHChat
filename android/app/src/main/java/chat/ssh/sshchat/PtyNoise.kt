package chat.ssh.sshchat

/** Drop prompt_toolkit / PTY echo noise (same idea as sshchat_gui._should_drop_line). */
object PtyNoise {
    private val timePrefix = Regex("""^\[\d{1,2}:\d{2}(?::\d{2})?]\s*""")
    private val promptPrefix = Regex("""(?:^|\s)>+ +\s*""")

    fun shouldDrop(raw: String): Boolean {
        var t = raw.trim()
        if (t.isEmpty()) return true
        // Real chat/system tags — never drop (incl. CSI crumbs like `[K[*] 9 …`).
        if ("[#" in t || "[*]" in t || t.startsWith("[OK]") || t.startsWith("[ERROR]")
            || t.startsWith("[INFO]") || t.startsWith("[+]") || t.startsWith("[-]") || t.startsWith("[!]")
        ) {
            return false
        }
        while (true) {
            val nxt = timePrefix.replaceFirst(t, "").trimStart()
            if (nxt == t) break
            t = nxt
        }
        if (t.startsWith("?[") && (t.endsWith("A") || t.endsWith("K"))) return true
        if (t.startsWith("WARNING: your terminal doesn't support cursor position requests")) return true
        // "> test" commit/redraw echoes — not chat content.
        if (t == ">" || t.startsWith("> ")) return true
        val stripped = t.trim()
        if (stripped.startsWith(">")) {
            val tail = promptPrefix.replaceFirst(stripped, "").trim()
            if (tail.isEmpty() || "[" !in tail) return true
        }
        return false
    }
}
