package chat.ssh.sshchat.library

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class DictCaptureTest {

    @Test
    fun capturesEnglishReplyAndLeavesOtherLinesAlone() {
        val d = DictCapture()
        assertFalse(d.feed("--- 英→中：hello ---"))
        d.begin("hello")
        assertFalse(d.feed("alice 加入了房间"))
        assertTrue(d.feed("--- 英→中：hello ---"))
        assertTrue(d.feed("英 [həˈləʊ]  美 [həˈloʊ]"))
        assertTrue(d.feed("  · int. 喂；你好"))
        assertFalse(d.feed("bob 离开了房间"))
        assertEquals(
            listOf("--- 英→中：hello ---", "英 [həˈləʊ]  美 [həˈloʊ]", "  · int. 喂；你好"),
            d.finish(),
        )
        assertFalse(d.isActive)
    }

    @Test
    fun capturesChineseBothBlocks() {
        val d = DictCapture()
        d.begin("你好")
        assertTrue(d.feed("--- 中→英：你好 ---"))
        assertTrue(d.feed("  · hello  —  你好"))
        assertTrue(d.feed(""))
        assertTrue(d.feed("--- 汉语：你好（现代汉语词典）---"))
        assertTrue(d.feed("你好  [nǐ hǎo]"))
        assertTrue(d.feed("  [形] 敬辞，用于打招呼"))
        assertEquals(6, d.finish()?.size)
    }

    @Test
    fun capturesServerError() {
        val d = DictCapture()
        d.begin("hello")
        assertTrue(d.feed("词典查询失败：网络不可用或超时，请稍后重试。"))
        assertEquals(listOf("词典查询失败：网络不可用或超时，请稍后重试。"), d.finish())
    }

    @Test
    fun finishBeforeReplyIsNull() {
        val d = DictCapture()
        d.begin("hello")
        assertNull(d.finish())
        assertTrue(d.isActive)
    }

    @Test
    fun normalizesSelection() {
        assertEquals("hello", DictCapture.normalize("  “hello,” "))
        assertEquals("ice cream", DictCapture.normalize("ice\n  cream."))
        assertEquals("你好", DictCapture.normalize("\u3000\u3000你好！"))
        assertNull(DictCapture.normalize(" ，。 "))
    }

    @Test
    fun commandPicksSafeMode() {
        assertEquals("/dict en en", DictCapture.command("en"))
        assertEquals("/dict en help", DictCapture.command("help"))
        assertEquals("/dict 你好", DictCapture.command("你好"))
        assertEquals("/dict cn 汉语", DictCapture.command("汉语"))
        assertEquals("/dict cn 帮助", DictCapture.command("帮助"))
    }
}
