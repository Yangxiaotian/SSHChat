package chat.ssh.sshchat

import org.bouncycastle.jce.provider.BouncyCastleProvider
import java.security.Security

/**
 * Android ships a stripped "BC" provider that lacks X25519 (used by sshj Curve25519 KEX).
 * Replace it with the full BouncyCastle we ship in the APK.
 */
object SshCrypto {
    @Volatile
    private var ready = false

    @Synchronized
    fun ensureInstalled() {
        if (ready) return
        val name = BouncyCastleProvider.PROVIDER_NAME
        val existing = Security.getProvider(name)
        if (existing == null || existing.javaClass != BouncyCastleProvider::class.java) {
            if (existing != null) {
                Security.removeProvider(name)
            }
            Security.insertProviderAt(BouncyCastleProvider(), 1)
        }
        ready = true
    }
}
