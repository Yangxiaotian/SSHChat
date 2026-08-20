package chat.ssh.sshchat

import net.schmizz.sshj.SSHClient
import net.schmizz.sshj.connection.channel.direct.Session
import net.schmizz.sshj.transport.verification.PromiscuousVerifier
import net.schmizz.sshj.userauth.keyprovider.KeyPairWrapper
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStreamWriter
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
    private val io = Executors.newSingleThreadExecutor()
    private val closed = AtomicBoolean(false)
    @Volatile private var ssh: SSHClient? = null
    @Volatile private var shell: Session.Shell? = null
    @Volatile private var writer: OutputStreamWriter? = null

    fun connect() {
        io.execute {
            try {
                onStatus("连接中 $host:$port …")
                SshCrypto.ensureInstalled()
                val client = SSHClient(AndroidSshConfig())
                client.addHostKeyVerifier(PromiscuousVerifier()) // ponytail: tryout; pin keys later
                client.connectTimeout = 20_000
                client.connect(host, port)
                client.authPublickey(username, KeyPairWrapper(keyPair))
                val session = client.startSession()
                session.allocatePTY("xterm", 80, 40, 0, 0, emptyMap())
                val sh = session.startShell()
                ssh = client
                shell = sh
                writer = OutputStreamWriter(sh.outputStream, Charsets.UTF_8)
                onStatus("已连接 $host:$port")
                val reader = BufferedReader(InputStreamReader(sh.inputStream, Charsets.UTF_8))
                while (!closed.get()) {
                    val line = reader.readLine() ?: break
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
        io.execute {
            try {
                val w = writer ?: return@execute
                val payload = if (text.endsWith("\n")) text else "$text\n"
                w.write(payload)
                w.flush()
            } catch (e: Exception) {
                onStatus("发送失败：${e.message}")
            }
        }
    }

    fun disconnect() {
        closed.set(true)
        closeQuietly()
        io.shutdownNow()
    }

    private fun closeQuietly() {
        try {
            writer?.close()
        } catch (_: Exception) {
        }
        writer = null
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
