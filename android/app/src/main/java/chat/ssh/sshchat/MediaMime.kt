package chat.ssh.sshchat

import java.io.File

/** Guess Content-Type from filename for upload / preview. */
object MediaMime {
    fun guess(name: String): String {
        val n = name.lowercase()
        return when {
            n.endsWith(".png") -> "image/png"
            n.endsWith(".jpg") || n.endsWith(".jpeg") -> "image/jpeg"
            n.endsWith(".gif") -> "image/gif"
            n.endsWith(".webp") -> "image/webp"
            n.endsWith(".bmp") -> "image/bmp"
            n.endsWith(".mp4") -> "video/mp4"
            n.endsWith(".webm") -> "video/webm"
            n.endsWith(".3gp") || n.endsWith(".3gpp") -> "video/3gpp"
            n.endsWith(".mkv") -> "video/x-matroska"
            n.endsWith(".m4a") -> "audio/mp4"
            n.endsWith(".aac") -> "audio/aac"
            n.endsWith(".mp3") -> "audio/mpeg"
            n.endsWith(".ogg") || n.endsWith(".oga") -> "audio/ogg"
            n.endsWith(".wav") -> "audio/wav"
            n.endsWith(".amr") -> "audio/amr"
            else -> "application/octet-stream"
        }
    }

    fun isImage(mime: String) = mime.startsWith("image/", ignoreCase = true)
    fun isVideo(mime: String) = mime.startsWith("video/", ignoreCase = true)
    fun isAudio(mime: String) = mime.startsWith("audio/", ignoreCase = true)

    fun kindLabel(mime: String, name: String = ""): String = when {
        isImage(mime) -> "图片"
        isVideo(mime) -> "视频"
        isAudio(mime) || name.lowercase().let {
            it.endsWith(".m4a") || it.endsWith(".aac") || it.endsWith(".mp3") || it.endsWith(".amr")
        } -> "语音"
        else -> "文件"
    }
}

fun File.guessMime(): String = MediaMime.guess(name)
