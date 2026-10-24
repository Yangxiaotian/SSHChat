package chat.ssh.sshchat

import org.json.JSONObject
import java.io.File
import java.io.FileInputStream
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.nio.charset.StandardCharsets

object SecureUpload {
    private const val CHUNK = 512 * 1024
    private const val ATTEMPTS = 4

    fun upload(url: String, key: String, file: File): String {
        val filename = file.name.replace('\\', '_').replace('/', '_').take(200).ifBlank { "file.bin" }
        val size = file.length()
        require(size > 0) { "empty file" }
        val count = ((size + CHUNK - 1) / CHUNK).toInt().coerceAtLeast(1)
        val encoded = URLEncoder.encode(filename, StandardCharsets.UTF_8.name()).replace("+", "%20")
        var remote = filename
        FileInputStream(file).use { input ->
            val buf = ByteArray(CHUNK)
            for (index in 0 until count) {
                val n = input.read(buf)
                require(n > 0) { "short file read" }
                val slice = if (n == buf.size) buf else buf.copyOf(n)
                var lastErr: Exception? = null
                var ok = false
                for (attempt in 0 until ATTEMPTS) {
                    try {
                        val got = postChunk(url, key, encoded, size, index, count, slice)
                        if (got.isNotBlank()) remote = got
                        ok = true
                        break
                    } catch (e: Exception) {
                        lastErr = e
                        Thread.sleep(400L * (attempt + 1))
                    }
                }
                if (!ok) throw (lastErr ?: IllegalStateException("upload failed"))
            }
        }
        return remote
    }

    private fun postChunk(
        url: String,
        key: String,
        encodedName: String,
        size: Long,
        index: Int,
        count: Int,
        data: ByteArray,
    ): String {
        val conn = (URL(url).openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            connectTimeout = 20_000
            readTimeout = 90_000
            doInput = true
            doOutput = true
            setRequestProperty("X-Upload-Key", key.uppercase())
            setRequestProperty("X-Upload-Index", index.toString())
            setRequestProperty("X-Upload-Count", count.toString())
            setRequestProperty("X-Upload-Size", size.toString())
            setRequestProperty("X-Upload-Filename", encodedName)
            setRequestProperty("Content-Type", "application/octet-stream")
            setFixedLengthStreamingMode(data.size)
        }
        conn.outputStream.use { it.write(data) }
        val code = conn.responseCode
        val stream = if (code in 200..299) conn.inputStream else conn.errorStream
        val raw = stream?.use { it.readBytes() }?.toString(StandardCharsets.UTF_8).orEmpty()
        if (code !in 200..299) {
            val err = runCatching { JSONObject(raw).optString("error") }.getOrNull()
                ?.takeIf { it.isNotBlank() }
                ?: raw.take(200).ifBlank { "HTTP $code" }
            error(err)
        }
        val payload = runCatching { JSONObject(raw.ifBlank { "{}" }) }.getOrNull()
        if (payload != null && payload.has("error") && payload.optString("error").isNotBlank()) {
            error(payload.optString("error"))
        }
        return payload?.optString("filename")?.takeIf { it.isNotBlank() } ?: ""
    }
}
