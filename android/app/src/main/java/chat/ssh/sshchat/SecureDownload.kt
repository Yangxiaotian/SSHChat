package chat.ssh.sshchat

import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.nio.charset.StandardCharsets

data class DownloadedMedia(
    val file: File,
    val name: String,
    val mime: String,
    val sender: String? = null,
) {
    val isImage: Boolean
        get() = MediaMime.isImage(mime)
    val isVideo: Boolean
        get() = MediaMime.isVideo(mime)
    val isAudio: Boolean
        get() = MediaMime.isAudio(mime)
}

object SecureDownload {
    fun fetch(pageUrl: String, key: String, outDir: File): DownloadedMedia {
        val (base, token) = splitDownloadUrl(pageUrl)
        outDir.mkdirs()
        val ticketBody = JSONObject().put("key", key.uppercase()).toString()
        val ticketJson = httpJson(
            "$base/download/$token/ticket",
            method = "POST",
            body = ticketBody,
            contentType = "application/json",
        )
        val filename = ticketJson.optString("filename").ifBlank { "file" }
        val mime = ticketJson.optString("mime").ifBlank {
            MediaMime.guess(filename)
        }
        val rel = ticketJson.optString("download")
        if (rel.isBlank()) error("no download ticket")
        val fileUrl = URL(URL(base + "/"), rel.removePrefix("/")).toString()
        val bytes = httpBytes(fileUrl)
        val safe = filename.replace(Regex("""[\\/]"""), "_").take(180).ifBlank { "file" }
        val out = File(outDir, "${System.currentTimeMillis()}-$safe")
        out.writeBytes(bytes)
        return DownloadedMedia(out, filename, mime)
    }

    private fun splitDownloadUrl(url: String): Pair<String, String> {
        val u = URL(url)
        val parts = u.path.split('/').filter { it.isNotEmpty() }
        require(parts.size >= 2 && parts[0] == "download") { "invalid download url" }
        val base = "${u.protocol}://${u.authority}"
        return base to parts[1]
    }

    private fun httpJson(
        url: String,
        method: String,
        body: String? = null,
        contentType: String? = null,
    ): JSONObject {
        val raw = String(httpBytes(url, method, body?.toByteArray(StandardCharsets.UTF_8), contentType), StandardCharsets.UTF_8)
        return JSONObject(raw.ifBlank { "{}" })
    }

    private fun httpBytes(
        url: String,
        method: String = "GET",
        body: ByteArray? = null,
        contentType: String? = null,
    ): ByteArray {
        val conn = (URL(url).openConnection() as HttpURLConnection).apply {
            requestMethod = method
            connectTimeout = 20_000
            readTimeout = 180_000
            doInput = true
            if (body != null) {
                doOutput = true
                if (contentType != null) setRequestProperty("Content-Type", contentType)
                outputStream.use { it.write(body) }
            }
        }
        val code = conn.responseCode
        val stream = if (code in 200..299) conn.inputStream else conn.errorStream
        val bytes = stream?.use { it.readBytes() } ?: ByteArray(0)
        if (code !in 200..299) {
            val msg = runCatching {
                JSONObject(String(bytes, StandardCharsets.UTF_8)).optString("error")
            }.getOrNull()?.takeIf { it.isNotBlank() }
                ?: String(bytes, StandardCharsets.UTF_8).take(200).ifBlank { "HTTP $code" }
            error(msg)
        }
        return bytes
    }
}
