package chat.ssh.sshchat

import android.content.ContentValues
import android.content.Context
import android.content.SharedPreferences
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import android.util.Base64
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import net.i2p.crypto.eddsa.EdDSAPrivateKey
import net.i2p.crypto.eddsa.EdDSAPublicKey
import net.i2p.crypto.eddsa.spec.EdDSANamedCurveTable
import net.i2p.crypto.eddsa.spec.EdDSAPrivateKeySpec
import net.i2p.crypto.eddsa.spec.EdDSAPublicKeySpec
import java.io.File
import java.security.KeyPair
import java.security.SecureRandom

/**
 * Ed25519 identity with three layers so reinstall keeps the same keypair:
 * 1) EncryptedSharedPreferences (normal use)
 * 2) Plain SharedPreferences mirrored for Android Auto Backup / device transfer
 * 3) Download/SSHChat/SSHChat-ed25519.identity (survives uninstall; restore via file if needed)
 */
object DeviceKeyStore {
    private const val PREFS = "sshchat_device_keys"
    private const val BACKUP_PREFS = "sshchat_identity_backup"
    private const val KEY_PRIVATE = "ed25519_private_b64"
    private const val KEY_PUBLIC_LINE = "openssh_public_line"
    private const val KEY_COMMENT = "key_comment"

    const val DURABLE_NAME = "SSHChat-ed25519.identity"
    private const val DURABLE_DIR = "SSHChat"

    data class Keys(
        val privateSeed: ByteArray,
        val publicOpenSshLine: String,
        val comment: String,
        val freshlyGenerated: Boolean,
        val restoredFromBackup: Boolean = false,
    )

    fun getOrCreate(context: Context): Keys {
        loadLocal(context)?.let { local ->
            persistAll(context, local.privateSeed, local.publicOpenSshLine, local.comment)
            return local
        }
        loadBackupPrefs(context)?.let { return installRestored(context, it) }
        readDurable(context)?.let { return installRestored(context, it) }
        return createNew(context)
    }

    /** Import identity file picked by the user (after reinstall when auto-find fails). */
    fun importFromUri(context: Context, uri: Uri): Keys {
        val raw = context.contentResolver.openInputStream(uri)?.use {
            it.readBytes().toString(Charsets.UTF_8)
        } ?: error("无法读取文件")
        val parsed = parseBody(raw) ?: error("不是有效的 SSHChat 身份备份")
        return installRestored(context, parsed)
    }

    fun createNew(context: Context): Keys {
        val generated = generateEd25519()
        val newComment = "sshchat-android@${Build.MODEL.replace("\\s+".toRegex(), "-")}"
        val pubLine = formatOpenSshPublicKey(generated.public as EdDSAPublicKey, newComment)
        val seed = (generated.private as EdDSAPrivateKey).seed
        persistAll(context, seed, pubLine, newComment)
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

    private fun installRestored(context: Context, keys: Keys): Keys {
        persistAll(context, keys.privateSeed, keys.publicOpenSshLine, keys.comment)
        return keys.copy(freshlyGenerated = false, restoredFromBackup = true)
    }

    private fun persistAll(context: Context, seed: ByteArray, pubLine: String, comment: String) {
        savePrefs(encryptedPrefs(context), seed, pubLine, comment)
        savePrefs(backupPrefs(context), seed, pubLine, comment)
        writeDurable(context, seed, pubLine, comment)
    }

    private fun loadLocal(context: Context): Keys? = readPrefs(encryptedPrefs(context))

    private fun loadBackupPrefs(context: Context): Keys? = readPrefs(backupPrefs(context))

    private fun readPrefs(prefs: SharedPreferences): Keys? {
        val existingPriv = prefs.getString(KEY_PRIVATE, null) ?: return null
        val existingPub = prefs.getString(KEY_PUBLIC_LINE, null) ?: return null
        val comment = prefs.getString(KEY_COMMENT, null) ?: return null
        val seed = runCatching { Base64.decode(existingPriv, Base64.NO_WRAP) }.getOrNull() ?: return null
        if (seed.size != 32) return null
        return Keys(seed, existingPub, comment, freshlyGenerated = false)
    }

    private fun savePrefs(prefs: SharedPreferences, seed: ByteArray, pubLine: String, comment: String) {
        prefs.edit()
            .putString(KEY_PRIVATE, Base64.encodeToString(seed, Base64.NO_WRAP))
            .putString(KEY_PUBLIC_LINE, pubLine)
            .putString(KEY_COMMENT, comment)
            .apply()
    }

    private fun encryptedPrefs(context: Context) = try {
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
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
    }

    private fun backupPrefs(context: Context) =
        context.getSharedPreferences(BACKUP_PREFS, Context.MODE_PRIVATE)

    private fun encodeBody(seed: ByteArray, pubLine: String, comment: String): String =
        "v1\n${Base64.encodeToString(seed, Base64.NO_WRAP)}\n$pubLine\n$comment\n"

    private fun parseBody(raw: String): Keys? {
        val lines = raw.lineSequence().map { it.trimEnd() }.filter { it.isNotEmpty() }.toList()
        if (lines.size < 4 || lines[0] != "v1") return null
        val seed = runCatching { Base64.decode(lines[1], Base64.NO_WRAP) }.getOrNull() ?: return null
        if (seed.size != 32) return null
        val pub = lines[2]
        val comment = lines[3]
        if (!pub.startsWith("ssh-ed25519 ")) return null
        return Keys(seed, pub, comment, freshlyGenerated = false, restoredFromBackup = true)
    }

    private fun writeDurable(context: Context, seed: ByteArray, pubLine: String, comment: String) {
        val body = encodeBody(seed, pubLine, comment).toByteArray(Charsets.UTF_8)
        runCatching {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                writeMediaStore(context, body)
            } else {
                writeLegacyFile(body)
            }
        }
    }

    private fun readDurable(context: Context): Keys? = runCatching {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            readMediaStore(context)
        } else {
            readLegacyFile()
        }
    }.getOrNull()

    private fun writeMediaStore(context: Context, body: ByteArray) {
        val resolver = context.contentResolver
        val existing = findMediaStoreUri(context)
        if (existing != null) {
            resolver.openOutputStream(existing, "wt")?.use { it.write(body) }
            return
        }
        val values = ContentValues().apply {
            put(MediaStore.Downloads.DISPLAY_NAME, DURABLE_NAME)
            put(MediaStore.Downloads.MIME_TYPE, "application/octet-stream")
            put(
                MediaStore.Downloads.RELATIVE_PATH,
                "${Environment.DIRECTORY_DOWNLOADS}/$DURABLE_DIR",
            )
            put(MediaStore.Downloads.IS_PENDING, 1)
        }
        val uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values) ?: return
        resolver.openOutputStream(uri)?.use { it.write(body) }
        values.clear()
        values.put(MediaStore.Downloads.IS_PENDING, 0)
        resolver.update(uri, values, null, null)
    }

    private fun readMediaStore(context: Context): Keys? {
        val uri = findMediaStoreUri(context) ?: return null
        val raw = context.contentResolver.openInputStream(uri)?.use {
            it.readBytes().toString(Charsets.UTF_8)
        } ?: return null
        return parseBody(raw)
    }

    private fun findMediaStoreUri(context: Context): Uri? {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return null
        val resolver = context.contentResolver
        resolver.query(
            MediaStore.Downloads.EXTERNAL_CONTENT_URI,
            arrayOf(MediaStore.Downloads._ID),
            "${MediaStore.Downloads.DISPLAY_NAME}=?",
            arrayOf(DURABLE_NAME),
            "${MediaStore.Downloads.DATE_MODIFIED} DESC",
        )?.use { c ->
            if (c.moveToFirst()) {
                val id = c.getLong(0)
                return Uri.withAppendedPath(MediaStore.Downloads.EXTERNAL_CONTENT_URI, id.toString())
            }
        }
        return null
    }

    @Suppress("DEPRECATION")
    private fun legacyFile(): File {
        val root = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
        return File(File(root, DURABLE_DIR), DURABLE_NAME)
    }

    private fun writeLegacyFile(body: ByteArray) {
        val file = legacyFile()
        file.parentFile?.mkdirs()
        file.writeBytes(body)
    }

    private fun readLegacyFile(): Keys? {
        val file = legacyFile()
        if (!file.isFile) return null
        return parseBody(file.readText(Charsets.UTF_8))
    }

    private fun generateEd25519(): KeyPair {
        val kpg = net.i2p.crypto.eddsa.KeyPairGenerator()
        kpg.initialize(256, SecureRandom())
        return kpg.generateKeyPair()
    }

    private fun formatOpenSshPublicKey(pub: EdDSAPublicKey, comment: String): String {
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
