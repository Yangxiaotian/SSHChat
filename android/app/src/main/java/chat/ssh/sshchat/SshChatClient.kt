package chat.ssh.sshchat

import net.schmizz.sshj.SSHClient
import net.schmizz.sshj.connection.channel.direct.Session
import net.schmizz.sshj.transport.verification.PromiscuousVerifier
import net.schmizz.sshj.userauth.keyprovider.KeyPairWrapper
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStream
import java.security.KeyPair
import java.util.concurrent.Executors
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Interactive SSH shell. All socket I/O stays off the Android main thread
 * (otherwise StrictMode throws NetworkOnMainThreadException and drops the session).
 */
class SshChatClient(
    private val host: String,
    private val port: Int,
    private val username: String,
    private val keyPair: KeyPair,
    private val onLine: (String) -> Unit,
    private val onStatus: (String) -> Unit,
    private val onDisconnected: (String?) -> Unit,
) {
    private val reader = Executors.newSingleThreadExecutor()
    private val writer = Executors.newSingleThreadExecutor()
    private val outbound = LinkedBlockingQueue<String>(64)
    private val closed = AtomicBoolean(false)
    @Volatile private var ssh: SSHClient? = null
    @Volatile private var shell: Session.Shell? = null
    @Volatile private var out: OutputStream? = null
    @Volatile private var writerStarted = false

    fun connect() {
        reader.execute {
            try {
                onStatus("连接中 $host:$port …")
                SshCrypto.ensureInstalled()
                val client = SSHClient(AndroidSshConfig())
                client.addHostKeyVerifier(PromiscuousVerifier()) // ponytail: tryout; pin keys later
                client.connectTimeout = 20_000
                client.connect(host, port)
                try {
                    client.socket?.keepAlive = true
                } catch (_: Exception) {
                }
                client.authPublickey(username, KeyPairWrapper(keyPair))
                // Keep the SSH transport alive even when the phone goes idle.
                // SSHJ keep-alive sends protocol-level heartbeats so NAT/Wi-Fi
                // idle reaping is less likely to drop the socket.
                try {
                    val ka = client.connection.keepAlive
                    if (ka != null) {
                        ka.setKeepAliveInterval(30) // seconds
                        if (!ka.isAlive) ka.start()
                    }
                } catch (_: Exception) {
                    // Best-effort: if keep-alive isn't supported/enabled on some
                    // builds, just continue without it.
                }
                val session = client.startSession()
                session.allocatePTY("xterm", 160, 48, 0, 0, emptyMap())
                val sh = session.startShell()
                ssh = client
                shell = sh
                out = sh.outputStream
                startWriter()
                onStatus("已连接 $host:$port")
                val br = BufferedReader(InputStreamReader(sh.inputStream, Charsets.UTF_8))
                while (!closed.get()) {
                    val line = br.readLine() ?: break
                    val cleaned = cleanLine(line)
                    if (cleaned.isNotEmpty()) {
                        onLine(cleaned)
                    }
                }
                if (!closed.get()) {
                    fail("连接已关闭")
                }
            } catch (e: Exception) {
                if (!closed.get()) {
                    fail(errText(e))
                }
            } finally {
                closeQuietly()
            }
        }
    }

    /** UI-safe: only enqueue; never touch sockets here. */
    fun send(text: String) {
        if (closed.get() || out == null) {
            onStatus("未连接，无法发送")
            return
        }
        val payload = if (text.endsWith("\n")) text else "$text\n"
        if (!outbound.offer(payload)) {
            onStatus("发送队列已满，请稍后再试")
        }
    }

    private fun startWriter() {
        if (writerStarted) return
        writerStarted = true
        writer.execute {
            try {
                while (!closed.get()) {
                    val payload = outbound.poll(200, TimeUnit.MILLISECONDS) ?: continue
                    val stream = out
                    val sh = shell
                    if (stream == null || sh == null || !sh.isOpen) {
                        fail("连接已断开")
                        break
                    }
                    try {
                        stream.write(payload.toByteArray(Charsets.UTF_8))
                        stream.flush()
                    } catch (e: Exception) {
                        fail(errText(e))
                        break
                    }
                }
            } catch (_: InterruptedException) {
                Thread.currentThread().interrupt()
            }
        }
    }

    private fun fail(detail: String) {
        if (closed.compareAndSet(false, true)) {
            onStatus("断开：$detail")
            onDisconnected(detail)
        }
        closeQuietly()
    }

    /** UI-safe: mark closed and tear down sockets on a background thread. */
    fun disconnect() {
        closed.set(true)
        outbound.clear()
        writer.execute {
            closeQuietly()
        }
        writer.shutdown()
        reader.shutdownNow()
    }

    private fun closeQuietly() {
        try {
            out?.close()
        } catch (_: Exception) {
        }
        out = null
        try {
            shell?.close()
        } catch (_: Exception) {
        }
        shell = null
        try {
            ssh?.disconnect()
        } catch (_: Exception) {
        }
        ssh = null
    }

    companion object {
        private val CSI = Regex("""\u001B\[[0-9;?]*[ -/]*[@-~]""")
        private val OSC = Regex("""\u001B\][^\u0007]*\u0007""")
        private val MANGLED_CSI = Regex("""\?\[[0-9;?]*[A-Za-z@-~]""")
        /** ESC fully lost: leftover `[2K` / `[K` / `[9;1H` (not `[*]` / `[#room]`). */
        private val BARE_CSI_FRAGMENT =
            Regex("""\[(?:\??(?:\d{1,4}(?:;\d{1,4})*)?)?[ABCDHJKSTfhlmnpqrstsu]""")

        fun errText(e: Throwable): String {
            val msg = e.message?.trim().orEmpty()
            if (msg.isNotEmpty() && msg != "null") return msg
            val cause = e.cause?.message?.trim().orEmpty()
            if (cause.isNotEmpty() && cause != "null") return cause
            return e.javaClass.simpleName.ifBlank { "unknown error" }
        }

        fun cleanLine(raw: String): String {
            var s = raw.replace("\r", "")
            s = CSI.replace(s, "")
            s = OSC.replace(s, "")
            s = MANGLED_CSI.replace(s, "")
            s = BARE_CSI_FRAGMENT.replace(s, "")
            s = s.replace("\u001B", "")
            // Keep leading spaces — board padding / 楚河汉界 centering depends on them.
            return s.trimEnd()
        }
    }
}
