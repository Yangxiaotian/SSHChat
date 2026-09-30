package chat.ssh.sshchat.games

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** 用真实服务端 show() 样例校验解析器。 */
class GameParserTest {
    @Test
    fun xiangqiParsesTenByNine() {
        val lines = """
            xiangqi 对局（playing）  红：alice   黑：bob
               1   2   3   4   5   6   7   8   9     ← 黑方 1～9
              图例：+红  -黑  !上一步  ·空  （请用等宽字体）
               -车 -马 -象 -士 -将 -士 -象 *   -车 
               ·   ·   ·   ·   ·   ·   ·   ·   ·   
               ·   -炮 ·   ·   ·   ·   !马 -炮 ·   
               -卒 ·   -卒 ·   -卒 ·   -卒 ·   -卒 
               ·   ·   ·   ·   ·   ·   ·   ·   ·   
                           楚河汉界
               ·   ·   ·   ·   ·   ·   ·   ·   ·   
               +兵 ·   +兵 ·   +兵 ·   +兵 ·   +兵 
               ·   +炮 ·   ·   +炮 ·   ·   ·   ·   
               ·   ·   ·   ·   ·   ·   ·   ·   ·   
               +车 +马 +相 +仕 +帅 +仕 +相 +马 +车 
               九  八  七  六  五  四  三  二  一    ← 红方纵线 九…一（右为一）
              上一步：马8进7
            轮到 红方 alice 走子
        """.trimIndent().lines()
        val snap = GameParser.parseLatest(lines, "alice")
        assertNotNull(snap)
        assertEquals(GameKind.XIANGQI, snap!!.kind)
        assertEquals(10, snap.board!!.rows)
        assertEquals(9, snap.board!!.cols)
        assertEquals("alice", snap.turnName)
        assertTrue(snap.board!!.cells.any { it == "r帅" })
        assertTrue(snap.board!!.cells.any { it == "b将" })
    }

    @Test
    fun gomokuParsesFifteen() {
        val lines = """
            gomoku 对局（playing）  黑：alice   白：bob
                1  2  3  4  5  6  7  8  9 10 11 12 13 14 15 
             1  .  .  .  .  .  .  .  .  .  .  .  .  .  .  . 
             2  .  .  .  .  .  .  .  .  .  .  .  .  .  .  . 
             3  .  .  .  .  .  .  .  .  .  .  .  .  .  .  . 
             4  .  .  .  .  .  .  .  .  .  .  .  .  .  .  . 
             5  .  .  .  .  .  .  .  .  .  .  .  .  .  .  . 
             6  .  .  .  .  .  .  .  .  .  .  .  .  .  .  . 
             7  .  .  .  .  .  .  .  .  .  .  .  .  .  .  . 
             8  .  .  .  .  .  .  .  # (o) .  .  .  .  .  . 
             9  .  .  .  .  .  .  .  .  .  .  .  .  .  .  . 
            10  .  .  .  .  .  .  .  .  .  .  .  .  .  .  . 
            11  .  .  .  .  .  .  .  .  .  .  .  .  .  .  . 
            12  .  .  .  .  .  .  .  .  .  .  .  .  .  .  . 
            13  .  .  .  .  .  .  .  .  .  .  .  .  .  .  . 
            14  .  .  .  .  .  .  .  .  .  .  .  .  .  .  . 
            15  .  .  .  .  .  .  .  .  .  .  .  .  .  .  . 
                1  2  3  4  5  6  7  8  9 10 11 12 13 14 15 
              上一步：(8, 9)
            轮到 黑方 alice 落子
        """.trimIndent().lines()
        val snap = GameParser.parseLatest(lines, "alice")
        assertNotNull(snap)
        assertEquals(GameKind.GOMOKU, snap!!.kind)
        assertEquals(15, snap.board!!.rows)
        assertEquals("B", snap.board!!.at(7, 7))
        assertEquals("W", snap.board!!.at(7, 8))
        assertTrue(snap.board!!.isLast(7, 8))
    }

    @Test
    fun holdemParsesHand() {
        val lines = """
            德州扑克 状态：进行中
            阶段：翻牌
            底池=5
            当前注=0
            公共牌：方块K 黑桃2 红桃K
            房主：alice
            #1 alice：积分=999 存活，已看牌
            #2 bob：积分=999 存活（行动中）
            你的手牌：梅花5 红桃J
        """.trimIndent().lines()
        val snap = GameParser.parseLatest(lines, "alice")
        assertNotNull(snap)
        assertEquals(GameKind.HOLDEM, snap!!.kind)
        assertEquals(listOf("♦K", "♠2", "♥K"), snap.community)
        assertEquals(listOf("♣5", "♥J"), snap.hand)
        assertEquals("5", snap.fields["pot"])
        assertEquals("bob", snap.turnName)
    }

    @Test
    fun doushouKeepsTerrain() {
        val lines = """
            doushou 对局（playing）  红：alice   黑：bob
            斗兽棋棋盘（7列×9行，+红 -黑，!上一步）
                 1   2   3   4   5   6   7  
             1  -狮  ·   黑陷  黑穴  黑陷  ·   -虎 
             2  ·   -狗  ·   黑陷  ·   -猫  ·  
             3  -鼠  ·   -豹  ·   -狼  ·   -象 
             4  ·   河   河   ·   河   河   ·  
             5  ·   河   河   ·   河   河   ·  
             6  ·   河   河   ·   河   河   ·  
             7  +象  ·   +狼  ·   +豹  ·   +鼠 
             8  ·   +猫  ·   红陷  ·   +狗  ·  
             9  +虎  ·   红陷  红穴  红陷  ·   +狮 
            图例：红穴/黑穴=兽穴；红陷/黑陷=陷阱；河=河流。坐标为 行 列（全局，左上仍为 1,1）。
            轮到 红方 alice 行棋
        """.trimIndent().lines()
        val snap = GameParser.parseLatest(lines, "alice")
        assertNotNull(snap)
        assertEquals(GameKind.DOUSHOU, snap!!.kind)
        assertEquals(9, snap.board!!.rows)
        assertEquals(7, snap.board!!.cols)
        assertEquals("黑穴", snap.board!!.at(0, 3))
        assertEquals("黑陷", snap.board!!.at(0, 2))
        assertEquals("河", snap.board!!.at(3, 1))
        assertEquals("红穴", snap.board!!.at(8, 3))
        assertEquals("红陷", snap.board!!.at(8, 2))
        assertEquals("b狮", snap.board!!.at(0, 0))
        assertEquals("r鼠", snap.board!!.at(6, 6))
    }

    @Test
    fun detectHeaders() {
        assertEquals(GameKind.SANGUO, GameParser.detectHeader("三国杀·军争 playing  4人  牌堆62")!!.first)
        assertEquals(GameKind.WEREWOLF, GameParser.detectHeader("werewolf state: night")!!.first)
        assertEquals(GameKind.MAHJONG, GameParser.detectHeader("麻将 状态：进行中")!!.first)
        assertEquals(GameKind.NIUTOU, GameParser.detectHeader("牛头王 状态：等待选行")!!.first)
    }
}
