package chat.ssh.sshchat

import com.hierynomus.sshj.transport.kex.DHGroups
import com.hierynomus.sshj.transport.kex.ExtInfoClientFactory
import com.hierynomus.sshj.transport.kex.ExtendedDHGroups
import net.schmizz.sshj.DefaultConfig
import net.schmizz.sshj.transport.kex.DHGexSHA1
import net.schmizz.sshj.transport.kex.DHGexSHA256
import net.schmizz.sshj.transport.kex.ECDHNistP

/**
 * Prefer NIST ECDH / DH over Curve25519 so we do not depend on BC's X25519
 * (Android's stock BC provider does not implement it).
 */
class AndroidSshConfig : DefaultConfig() {
    override fun initKeyExchangeFactories() {
        setKeyExchangeFactories(
            DHGexSHA256.Factory(),
            ECDHNistP.Factory521(),
            ECDHNistP.Factory384(),
            ECDHNistP.Factory256(),
            DHGexSHA1.Factory(),
            DHGroups.Group14SHA256(),
            DHGroups.Group14SHA1(),
            DHGroups.Group15SHA512(),
            DHGroups.Group16SHA512(),
            ExtendedDHGroups.Group14SHA256AtSSH(),
            ExtendedDHGroups.Group15SHA256(),
            ExtInfoClientFactory(),
        )
    }
}
