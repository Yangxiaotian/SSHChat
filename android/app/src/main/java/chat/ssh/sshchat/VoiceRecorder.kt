package chat.ssh.sshchat

import android.content.Context
import android.media.MediaRecorder
import android.os.Build
import android.os.SystemClock
import java.io.File

/** Push-to-talk AAC-in-MP4 (.m4a) recorder. */
class VoiceRecorder(private val context: Context, private val outDir: File) {
    private var recorder: MediaRecorder? = null
    private var target: File? = null
    private var startedAt = 0L

    val isRecording: Boolean get() = recorder != null

    fun start(): File {
        cancel()
        outDir.mkdirs()
        val file = File(outDir, "voice-${System.currentTimeMillis()}.m4a")
        val mr = if (Build.VERSION.SDK_INT >= 31) {
            MediaRecorder(context)
        } else {
            @Suppress("DEPRECATION")
            MediaRecorder()
        }
        try {
            mr.setAudioSource(MediaRecorder.AudioSource.MIC)
            mr.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
            mr.setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
            mr.setAudioEncodingBitRate(64_000)
            mr.setAudioSamplingRate(44_100)
            mr.setOutputFile(file.absolutePath)
            mr.prepare()
            mr.start()
        } catch (e: Exception) {
            runCatching { mr.release() }
            file.delete()
            throw e
        }
        recorder = mr
        target = file
        startedAt = SystemClock.elapsedRealtime()
        return file
    }

    /** Stop and return file if duration >= [minMs]; otherwise delete and return null. */
    fun stop(minMs: Long = 400L): File? {
        val file = target
        val mr = recorder
        recorder = null
        target = null
        val elapsed = SystemClock.elapsedRealtime() - startedAt
        try {
            mr?.stop()
        } catch (_: Exception) {
            // stop() can throw if too short
        } finally {
            runCatching { mr?.release() }
        }
        if (file == null) return null
        if (elapsed < minMs || !file.isFile || file.length() < 200L) {
            file.delete()
            return null
        }
        return file
    }

    fun cancel() {
        val file = target
        val mr = recorder
        recorder = null
        target = null
        try {
            mr?.stop()
        } catch (_: Exception) {
        } finally {
            runCatching { mr?.release() }
        }
        file?.delete()
    }
}
