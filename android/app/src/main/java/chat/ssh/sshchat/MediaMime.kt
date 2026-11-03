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
            n.endsWith(".weba") -> "audio/webm"
            // Guest/browser MediaRecorder often lands as voice-*.webm.
            n.endsWith(".webm") && isVoiceName(n) -> "audio/webm"
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

    fun isVoiceName(name: String): Boolean {
        val n = name.lowercase().substringAfterLast('/').substringAfterLast('\\')
        return n.startsWith("voice") || n.contains("voice-")
    }

    fun isImage(mime: String) = mime.startsWith("image/", ignoreCase = true)
    fun isVideo(mime: String): Boolean {
        if (!mime.startsWith("video/", ignoreCase = true)) return false
        return true
    }

    fun isAudio(mime: String, name: String = ""): Boolean {
        if (mime.startsWith("audio/", ignoreCase = true)) return true
        val n = name.lowercase().substringAfterLast('/').substringAfterLast('\\')
        if (n.endsWith(".m4a") || n.endsWith(".aac") || n.endsWith(".mp3") ||
            n.endsWith(".amr") || n.endsWith(".wav") || n.endsWith(".oga") ||
            n.endsWith(".weba")
        ) {
            return true
        }
        // Ticket may still say video/webm for older guest voice notes.
        if ((n.endsWith(".webm") || n.endsWith(".ogg")) && isVoiceName(n)) return true
        if (mime.equals("video/webm", ignoreCase = true) && isVoiceName(n)) return true
        return false
    }

    fun kindLabel(mime: String, name: String = ""): String = when {
        isImage(mime) -> "图片"
        isAudio(mime, name) -> "语音"
        isVideo(mime) -> "视频"
        else -> "文件"
    }
}

fun File.guessMime(): String = MediaMime.guess(name)
