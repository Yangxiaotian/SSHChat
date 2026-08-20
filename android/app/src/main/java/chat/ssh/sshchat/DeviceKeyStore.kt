package chat.ssh.sshchat

import android.content.Context
import android.util.Base64
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import net.i2p.crypto.eddsa.EdDSAPrivateKey
import net.i2p.crypto.eddsa.EdDSAPublicKey
import net.i2p.crypto.eddsa.spec.EdDSANamedCurveTable
import net.i2p.crypto.eddsa.spec.EdDSAPrivateKeySpec
import net.i2p.crypto.eddsa.spec.EdDSAPublicKeySpec
import java.security.KeyPair
import java.security.SecureRandom

/**
 * First launch after install: generate Ed25519 keypair once and keep it in app-private storage.
 * Android cannot run code during APK install — first open is the practical equivalent.
 */
object DeviceKeyStore {
    private const val PREFS = "sshchat_device_keys"
    private const val KEY_PRIVATE = "ed25519_private_b64"
    private const val KEY_PUBLIC_LINE = "openssh_public_line"
    private const val KEY_COMMENT = "key_comment"

    data class Keys(
        val privateSeed: ByteArray,
        val publicOpenSshLine: String,
        val comment: String,
        val freshlyGenerated: Boolean,
    )

    fun getOrCreate(context: Context): Keys {
        val prefs = prefs(context)
        val existingPriv = prefs.getString(KEY_PRIVATE, null)
        val existingPub = prefs.getString(KEY_PUBLIC_LINE, null)
        val comment = prefs.getString(KEY_COMMENT, null)
        if (existingPriv != null && existingPub != null && comment != null) {
            return Keys(
                privateSeed = Base64.decode(existingPriv, Base64.NO_WRAP),
                publicOpenSshLine = existingPub,
                comment = comment,
                freshlyGenerated = false,
            )
        }

        val generated = generateEd25519()
        val newComment = "sshchat-android@${android.os.Build.MODEL.replace("\\s+".toRegex(), "-")}"
        val pubLine = formatOpenSshPublicKey(generated.public as EdDSAPublicKey, newComment)
        val seed = (generated.private as EdDSAPrivateKey).seed

        prefs.edit()
            .putString(KEY_PRIVATE, Base64.encodeToString(seed, Base64.NO_WRAP))
            .putString(KEY_PUBLIC_LINE, pubLine)
            .putString(KEY_COMMENT, newComment)
            .apply()

        return Keys(
            privateSeed = seed,
            publicOpenSshLine = pubLine,
            comment = newComment,
            freshlyGenerated = true,
        )
    }

    fun toKeyPair(seed: ByteArray): KeyPair {
        val spec = EdDSANamedCurveTable.getByName(EdDSANamedCurveTable.ED_25519)
        val privSpec = EdDSAPrivateKeySpec(seed, spec)
        val priv = EdDSAPrivateKey(privSpec)
        val pub = EdDSAPublicKey(EdDSAPublicKeySpec(priv.a, spec))
        return KeyPair(pub, priv)
    }

    private fun prefs(context: Context) = try {
        val master = MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        EncryptedSharedPreferences.create(
            context,
            PREFS,
            master,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    } catch (_: Exception) {
        // Fallback if encrypted prefs fail on odd devices — still app-private.
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
    }

    private fun generateEd25519(): KeyPair {
        val kpg = net.i2p.crypto.eddsa.KeyPairGenerator()
        kpg.initialize(256, SecureRandom())
        return kpg.generateKeyPair()
    }

    private fun formatOpenSshPublicKey(pub: EdDSAPublicKey, comment: String): String {
        // OpenSSH ed25519 public key blob: string "ssh-ed25519" + string key(32)
        val keyBytes = pub.abyte
        val type = "ssh-ed25519".toByteArray(Charsets.US_ASCII)
        val blob = ByteArray(4 + type.size + 4 + keyBytes.size)
        var off = 0
        fun writeString(data: ByteArray) {
            blob[off++] = ((data.size ushr 24) and 0xff).toByte()
            blob[off++] = ((data.size ushr 16) and 0xff).toByte()
            blob[off++] = ((data.size ushr 8) and 0xff).toByte()
            blob[off++] = (data.size and 0xff).toByte()
            System.arraycopy(data, 0, blob, off, data.size)
            off += data.size
        }
        writeString(type)
        writeString(keyBytes)
        val b64 = Base64.encodeToString(blob, Base64.NO_WRAP)
        return "ssh-ed25519 $b64 $comment"
    }
}
