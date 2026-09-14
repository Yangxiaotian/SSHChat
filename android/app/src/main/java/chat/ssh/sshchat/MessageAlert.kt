package chat.ssh.sshchat

import android.content.Context
import android.media.AudioManager
import android.media.ToneGenerator
import android.os.Build
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager

/**
 * Short receive tone for peer chat / PM / join-leave (same rules as desktop GUI).
 */
object MessageAlert {
    @Volatile private var lastPlayAtMs = 0L
    private const val MIN_INTERVAL_MS = 350L

    @Volatile private var recentOutboundBody: String = ""
    @Volatile private var recentOutboundAtMs: Long = 0L

    /** Call right before sending so our own echo does not chime. */
    fun noteOutbound(body: String) {
        val b = body.trim()
        if (b.isEmpty()) return
        recentOutboundBody = b
        recentOutboundAtMs = System.currentTimeMillis()
    }

    fun playIfNeeded(context: Context, line: String, myName: String) {
        if (!ChatLineParsers.shouldAlert(
                line,
                myName,
                recentOutboundBody = recentOutboundBody,
                recentOutboundAtMs = recentOutboundAtMs,
            )
        ) {
            return
        }
        play(context)
    }

    fun play(context: Context) {
        val now = System.currentTimeMillis()
        if (now - lastPlayAtMs < MIN_INTERVAL_MS) return
        lastPlayAtMs = now
        try {
            val tg = ToneGenerator(AudioManager.STREAM_NOTIFICATION, 80)
            tg.startTone(ToneGenerator.TONE_PROP_ACK, 180)
            android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({
                runCatching { tg.release() }
            }, 250)
        } catch (_: Exception) {
            // ignore missing audio path
        }
        vibrate(context)
    }

    private fun vibrate(context: Context) {
        try {
            if (Build.VERSION.SDK_INT >= 31) {
                val vm = context.getSystemService(VibratorManager::class.java) ?: return
                vm.defaultVibrator.vibrate(
                    VibrationEffect.createOneShot(40, VibrationEffect.DEFAULT_AMPLITUDE),
                )
            } else {
                @Suppress("DEPRECATION")
                val v = context.getSystemService(Context.VIBRATOR_SERVICE) as? Vibrator ?: return
                if (Build.VERSION.SDK_INT >= 26) {
                    v.vibrate(VibrationEffect.createOneShot(40, VibrationEffect.DEFAULT_AMPLITUDE))
                } else {
                    @Suppress("DEPRECATION")
                    v.vibrate(40)
                }
            }
        } catch (_: Exception) {
        }
    }
}
