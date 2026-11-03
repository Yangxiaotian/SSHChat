package chat.ssh.sshchat

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class MediaMimeTest {
    @Test
    fun voiceWebmGuessedAsAudio() {
        assertEquals("audio/webm", MediaMime.guess("voice-123.webm"))
        assertEquals("audio/webm", MediaMime.guess("voice.webm"))
        assertEquals("video/webm", MediaMime.guess("clip.webm"))
    }

    @Test
    fun voiceWebmDetectedAsAudioEvenIfTicketSaysVideo() {
        assertTrue(MediaMime.isAudio("video/webm", "voice-9.webm"))
        assertTrue(MediaMime.isAudio("audio/webm", "voice-9.webm"))
        assertTrue(MediaMime.isAudio("audio/wav", "voice-9.wav"))
        assertFalse(MediaMime.isAudio("video/webm", "clip.webm"))
    }

    @Test
    fun kindLabelPrefersVoiceOverVideoForGuestNotes() {
        assertEquals("语音", MediaMime.kindLabel("video/webm", "voice-1.webm"))
        assertEquals("语音", MediaMime.kindLabel("audio/wav", "voice-1.wav"))
        assertEquals("视频", MediaMime.kindLabel("video/webm", "clip.webm"))
    }
}
