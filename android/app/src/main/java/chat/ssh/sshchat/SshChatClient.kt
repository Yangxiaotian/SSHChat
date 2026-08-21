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
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Interactive SSH shell to the bundled chat host (ForceCommand / login shell).
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
    private val writeLock = Any()
    private val closed = AtomicBoolean(false)
    @Volatile private var ssh: SSHClient? = null
    @Volatile private var shell: Session.Shell? = null
    @Volatile private var out: OutputStream? = null

    fun connect() {
        reader.execute {
            try {
                onStatus("连接中 $host:$port …")
                SshCrypto.ensureInstalled()
                val client = SSHClient(AndroidSshConfig())
                client.addHostKeyVerifier(PromiscuousVerifier()) // ponytail: tryout; pin keys later
                client.connectTimeout = 20_000
                client.connect(host, port)
                client.authPublickey(username, KeyPairWrapper(keyPair))
                val session = client.startSession()
                // Wide PTY so ASCII go/chess boards don't wrap on phones.
                session.allocatePTY("xterm", 160, 48, 0, 0, emptyMap())
                val sh = session.startShell()
                ssh = client
                shell = sh
                out = sh.outputStream
                onStatus("已连接 $host:$port")
                val br = BufferedReader(InputStreamReader(sh.inputStream, Charsets.UTF_8))
                while (!closed.get()) {
                    val line = br.readLine() ?: break
                    val cleaned = cleanLine(line)
                    if (cleaned.isNotEmpty()) {
                        onLine(cleaned)
                    }
                }
                onDisconnected(null)
            } catch (e: Exception) {
                if (!closed.get()) {
                    onDisconnected(e.message ?: e.toString())
                }
            } finally {
                closeQuietly()
            }
        }
    }

    fun send(text: String) {
        // Must not queue on the reader thread — it blocks in readLine forever.
        try {
            val stream = out ?: return
            val payload = if (text.endsWith("\n")) text else "$text\n"
            val bytes = payload.toByteArray(Charsets.UTF_8)
            synchronized(writeLock) {
                stream.write(bytes)
                stream.flush()
            }
        } catch (e: Exception) {
            onStatus("发送失败：${e.message}")
        }
    }

    fun disconnect() {
        closed.set(true)
        closeQuietly()
        reader.shutdownNow()
    }

    private fun closeQuietly() {
        synchronized(writeLock) {
            try {
                out?.close()
            } catch (_: Exception) {
            }
            out = null
        }
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

        fun cleanLine(raw: String): String {
            var s = raw.replace("\r", "")
            s = CSI.replace(s, "")
            s = OSC.replace(s, "")
            s = s.replace("\u001B", "")
            return s.trim()
        }
    }
}
