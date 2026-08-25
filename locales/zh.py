"""Chinese (zh) UI message catalog for SSHChat."""

MESSAGES: dict = {
    "help_lines": [
        "[*] ---------- SSHChat 命令说明 ----------\n",
        "[*] 普通文字（不以 / 开头）发到「当前活跃房间」，房内在线用户都会收到。\n",
        "[*]\n",
        "[*] /join <房间>     加入房间并立刻切到该房；若已在房内则只切换当前房。\n",
        "[*]              房间名：1～32 字符，仅字母、数字、下划线、连字符。\n",
        "[*] /switch <房间>  只在已加入的房间之间切换；未加入会提示先用 /join。\n",
        "[*] /part <房间>    退出某房间；至少保留一间，不能退出最后一个。\n",
        "[*] /rooms         列出你已加入的房间；前面带 * 的是当前活跃房间。\n",
        "[*] /names 或 /users  列出当前活跃房间内的昵称（二者相同）。\n",
        "[*]\n",
        "[*] /msg #<房间> <文字>   不切换当前房，把一句话发到指定房间（# 开头表示房间）。\n",
        "[*] /msg <昵称> <文字>   私聊：对方在线则即时送达；不在线则留言，对方下次上线时收到。\n",
        "[*]              昵称大小写不敏感；同昵称多人在线会全部收到；发件人会收到汇总提示。\n",
        "[*] /leave [昵称]     查看你发出、对方尚未阅读的留言/文件（按昵称分组编号）。\n",
        "[*] /leave <昵称> <编号>  撤回发给该昵称的第 N 条未读留言或离线文件（别名：/留言、/unmsg）。\n",
        "[*]\n",
        "[*] /clear 或 /cls  清屏（终端会清空显示；图形客户端会清空当前房间记录）。\n",
        "[*] /announce      查看当前房间公告；房主可用 /announce <文字> 设置，/announce clear 清除。\n",
        "[*]              房主：#default 为第一个进服用户；其它房间为第一个 /join 该房的用户。\n",
        "[*] /lang [en|zh]   切换界面语言（默认英文；偏好按昵称保存）。\n",
        "[*]\n",
        "[*] /game ...      房间小游戏（chess、gomoku、xiangqi、sanguo）。/game list /new /join …；房主 /game on|off 上下线。\n",
        "[*]              详细用法用 /game help 查看。\n",
        "[*] /news [中文|国际|科技|all] [条数]  从 RSS 查看标题与提要正文；默认每类 3 条。\n",
        "[*] /news detail <分类> <序号>  更长提要（RSS 内；别名：详情）。\n",
        "[*] /news fetch <分类> <序号>  按 RSS 链接抓取网页正文（别名：全文；非 JS 站、可能截断）。\n",
        "[*] /library       列出图书馆书目（epub / txt / md / pdf；每人自带书签，翻页自动保存）。\n",
        "[*] /lib             /library 的简写。\n",
        "[*] /library open <序号|文件名>  打开图书（有书签则从书签继续）；next|prev|page 翻页。\n",
        "[*] /library find <关键词>        按书名查找书目；阅读中则在当前书中检索（别名：search / 搜索 / 查找）。\n",
        "[*] /dict en|cn|hh <词>  词典：英→中、中→英、汉语释义；/dict <词> 自动识别。\n",
        "[*]\n",
        "[*] /sendfile      发送文件到当前房间，你将收到上传网址，密钥另行单独给出。\n",
        "[*] /sendfile <昵称>    发送文件给指定用户（对方离线则留言，上线后收到；可用 /leave 查看或撤回）。\n",
        "[*] /sendfile #<房间>   发送文件到指定房间，成员各自收到不同的下载网址+密钥。\n",
        "[*]              文件名以你实际上传的文件为准，不必在指令里写。\n",
        "[*]              密钥不在网址里，打开网页后另行输入；支持图片、视频、PDF等在线预览。\n",
        "[*]              上传和下载都只能用一次，用过即作废，链接被别人截获也没用。\n",
        "[*] /canvas        当前房间共享画板（网页；别名 /board）；每人独立网址+密钥，解锁后共同绘画。\n",
        "[*] /canvas <昵称>    与某位在线用户开私密画板。\n",
        "[*] /canvas #<房间>   在指定房间开共享画板（你必须在该房内）。\n",
        "[*] /canvas close     发起人关闭当前房间画板；/canvas new 强制新开（即使房间已有）。\n",
        "[*]              密钥不在网址里，打开网页后另行输入；笔画实时同步。详细用法：/canvas help。\n",
        "[*]              图形客户端会自动打开画板；终端请把网址复制到浏览器。\n",
        "[*] /help          显示本说明。\n",
    ],
    "game_help_lines": [
        "[*] /game list             列出本房已上线、可玩的游戏。",
        "[*] /game new <名称>       在当前房间开一局；发起人坐第一席"
        "（chess: 白；gomoku/go/xiangqi/doushou: 黑/黑/红/红先手；sanguo: 房主）。",
        "[*] /game new <名称> ai [easy|normal|hard]  棋类开启 AI 练习局（仅 chess/gomoku/xiangqi）；"
        "练习局不计入持久化积分。",
        "[*] /game join             加入对局（chess/gomoku/go/xiangqi/doushou 为第二席；"
        "sanguo 可 2～6 人 join，房主 /game move 开始 开局）。",
        "[*] /game seats            显示双方与对局状态。",
        "[*] /game show             重新显示棋盘（己方在下，对手视角自动翻转）。",
        "[*] /game rating [游戏] [昵称]  查看棋类持久化积分/等级；积分跨房间共享。",
        "[*] chess 棋盘用 Unicode 棋子（♔♟ 等）；空位为 ·，上一步格子用括号标出。"
        "请用等宽字体；深色背景下黑子若看不清可换浅色终端主题。",
        "[*] /game move …           chess: SAN/UCI；gomoku/go: 行 列；go 可 pass 停一手；"
        "xiangqi: 棋谱（炮二平五、马2进3）或坐标四元组；"
        "doushou: 坐标四元组（起行 起列 终行 终列）；"
        "sanguo: 军争版；等待时房主 开始；/game move 武将 查武将池；"
        "观星/蛊惑/断粮等技能见 /game show（别名 sgs/三国杀）。",
        "[*] xiangqi 也可用别名 cchess 开局。",
        "[*] 棋盘 +红 -黑 !上一步；马/象/士进退按纵线朝棋盘中线为进。",
        "[*] 象棋按竞赛规则：三次循环局面时，长将/长捉不变着判负；双方无照打可和棋。",
        "[*] /game pgn              导出当前/已结束棋局的 PGN（仅 chess）。",
        "[*] /game undo             悔棋：上一步走子方发起，对方 /game undo accept 同意后撤销一步"
        "（chess/gomoku/go/xiangqi/doushou；简写 acc / rej / can；reject 拒绝，cancel 取消请求）。",
        "[*] /game resign           认负（仅对局进行中）。",
        "[*] /game abort            终止未开始的对局。",
        "[*] /game end              房主可强制结束当前对局。",
        "[*] /game restore          把因重启/联邦冲突暂存的对局恢复到空房间（别名：恢复）。",
        "[*] /game on <名称>        房主在本房上线某游戏（别名同 new）。",
        "[*] /game off <名称>       房主在本房下线某游戏（进行中的该局不受影响）。",
        "[*] holdem（德州扑克）中英指令对照：",
        "[*]   开始 start | 看牌 look | 过牌 check | 跟注 call | 加注 <额> raise <额> | 弃牌 fold | 全下 allin",
        "[*]   机器人 bot <easy|hard|pro>；开局后 /game show 帮助 可再看完整说明。",
        "[*] zjh（炸金花）中英对照：开始 start | 看牌 look | 跟注 follow | 加注 raise <额> | "
        "比牌 compare <昵称> | 弃牌 fold；比牌费用为当前单注两倍（看牌后再翻倍）；"
        "牌型：豹子>顺金>金花>顺子>对子>单张，花色不同235可胜豹子；"
        "同牌型相等时主动比牌者负；每局结束自动发下一局；"
        "需机器人时用 start bot 或 bot add [人数]；bot <easy|hard|pro> 设难度。",
        "[*] mahjong（麻将）4 人局：人数不足时 start 自动补 AI；房主可 bot <easy|hard|pro> 调难度。",
        "[*] 支持吃/碰/杠/点炮胡/自摸胡；轮到你时 discard <牌>，可 gang/hu；他人弃牌后可 chi/peng/gang/hu/pass。",
        "[*] 麻将编码说明：m=万（man），p=筒/饼（pin），s=条/索（sou），z=字牌（东南西北中发白）。",
        "[*] 麻将支持中文出牌：二万、九筒、五条、东风、红中、发财、白板（也支持 m1/p9/s5/z3）。",
    ],
    "server": {
        "announce_preview": "[#{room}] [*] 公告：{text}\n",
        "announce_current": "[*] #{room} 当前公告：{text}\n",
        "announce_none": "[*] #{room} 暂无公告。\n",
        "announce_owner_only": "[*] 只有房主可以修改公告（查看无需权限）。\n",
        "announce_cleared_bcast": "[#{room}] [*] 公告已清除。\n",
        "announce_cleared": "[*] 已清除 #{room} 的公告。\n",
        "announce_too_long": "[*] 公告过长（最多 {max_len} 字符）。\n",
        "announce_updated": "[*] 已更新 #{room} 的公告。\n",
        "announce_set_bcast": "[#{room}] [*] 公告：{text}\n",
        "offline_header": "[*] 你有 {n} 条留言（离线期间收到，按时间顺序）：\n",
        "offline_file_meta": "[*] （离线文件 {when}，来自 {sender}）\n",
        "offline_file_pm": "[PM from {sender}] (离线文件 {when}) {text}\n",
        "offline_pm": "[PM from {sender}] (留言 {when}) {text}\n",
        "leave_none": "[*] 你目前没有对方尚未阅读的留言或文件。\n",
        "leave_list_header": "[*] 你发出的未读留言/文件（共 {n} 条）：\n",
        "leave_recall_hint": "[*] 撤回：/leave {recipient} <编号>\n",
        "leave_group": "[*] → {name}:\n",
        "leave_item": "[*]   {index}. ({when}) {text}\n",
        "leave_usage": (
            "[*] Usage: /leave [nick]  |  /leave <nick> <n>\n"
            "[*] （列出或撤回你发出、对方尚未阅读的留言或离线文件）\n"
        ),
        "leave_bad_index": "[*] 编号须为正整数。\n",
        "leave_recalled": (
            "[*] 已撤回发给 {recipient} 的第 {index} 条{kind}"
            "（{when}）：{text}\n"
        ),
        "leave_recall_fail": (
            "[*] 撤回失败：没有发给 {recipient} 的第 {index} 条未读留言/文件"
            "（可用 /leave {recipient} 查看）。\n"
        ),
        "fed_connected": (
            "[*] 联邦网络已连接 {n} 个节点（同名用户/房间跨服合并）。\n"
        ),
        "multi_terminal": (
            "[*] 检测到同账号其他终端在线，已同步房间并支持直接续玩。\n"
        ),
        "session_restored": (
            "[*] 已恢复上次客户端会话，回到原房间；如有未结束对局可继续操作。\n"
        ),
        "game_seat_resumed": "[*] 已接管旧连接保留的游戏席位。\n",
        "game_resume_takeover": "已自动续玩接管旧终端席位。",
        "game_usage_header": "[*] /game 用法：\n",
        "game_room_playable": "[*] 本房可玩：{games}\n",
        "game_list_line": (
            "[*] 可玩游戏：{games}"
            "（xiangqi 别名 cchess；sanguo 别名 sgs/三国杀）\n"
        ),
        "game_list_empty": (
            "[*] 本房暂无已上线游戏；房主可用 /game on <名称> 上线。\n"
        ),
        "game_forward_fail": "[*] 无法连接对局所在节点，请稍后重试。\n",
        "game_cmd_fail": (
            "[*] /game 命令执行失败，请稍后重试（详情见服务端日志）。\n"
        ),
        "news_cmd_fail": (
            "[*] 新闻命令处理失败，请稍后重试（详情见服务端日志）。\n"
        ),
        "library_cmd_fail": (
            "[*] 图书馆命令处理失败，请稍后重试（详情见服务端日志）。\n"
        ),
        "lang_usage": "[*] 用法：/lang [en|zh]\n",
        "lang_set": "[*] 界面语言已设为 {lang}。\n",
        "lang_current": "[*] 当前界面语言：{lang}\n",
        "rating_no_persist": "[*] {game} 当前没有持久化棋类积分。\n",
    },
}
