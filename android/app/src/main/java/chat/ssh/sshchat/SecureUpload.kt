package chat.ssh.sshchat

import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.nio.charset.StandardCharsets
import java.util.UUID

object SecureUpload {
    fun upload(url: String, key: String, file: File): String {
        val filename = file.name.replace('\\', '_').replace('/', '_').take(200).ifBlank { "file.bin" }
        val mime = MediaMime.guess(filename).let {
            if (it == "application/octet-stream") "application/octet-stream" else it
        }
        val boundary = "----SSHChat${UUID.randomUUID().toString().replace("-", "")}"
        val head = (
            "--$boundary\r\n" +
                "Content-Disposition: form-data; name=\"file\"; filename=\"$filename\"\r\n" +
                "Content-Type: $mime\r\n\r\n"
            ).toByteArray(StandardCharsets.UTF_8)
        val tail = "\r\n--$boundary--\r\n".toByteArray(StandardCharsets.UTF_8)
        val data = file.readBytes()

        val conn = (URL(url).openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            connectTimeout = 20_000
            readTimeout = 180_000
            doInput = true
            doOutput = true
            setRequestProperty("X-Upload-Key", key.uppercase())
            setRequestProperty("Content-Type", "multipart/form-data; boundary=$boundary")
            setFixedLengthStreamingMode(head.size + data.size + tail.size)
        }
        conn.outputStream.use { out ->
            out.write(head)
            out.write(data)
            out.write(tail)
        }
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
        return payload?.optString("filename")?.takeIf { it.isNotBlank() } ?: filename
    }
}
