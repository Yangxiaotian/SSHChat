"""English (en) UI message catalog for SSHChat."""

MESSAGES: dict = {
    "help_lines": [
        "[*] ---------- SSHChat command help ----------\n",
        "[*] Plain text (not starting with /) goes to your active room; everyone online there receives it.\n",
        "[*]\n",
        "[*] /join <room>     Join a room and switch to it; if already joined, only switch active room.\n",
        "[*]              Room name: 1–32 chars; letters, digits, underscore, hyphen only.\n",
        "[*] /switch <room>  Switch among rooms you already joined; prompts /join if not a member.\n",
        "[*] /part <room>    Leave a room; you must keep at least one (cannot leave the last).\n",
        "[*] /rooms         List rooms you joined; * marks the active room.\n",
        "[*] /names or /users  List nicknames in the active room (same command).\n",
        "[*]\n",
        "[*] /msg #<room> <text>   Send one line to a room without switching (# means room).\n",
        "[*] /msg <nick> <text>   PM: delivered live if online; otherwise left as a leave-message for next login.\n",
        "[*]              Nick match is case-insensitive; all online peers with that nick get it; you get a summary.\n",
        "[*] /leave [nick]     List leave-messages/files you sent that the recipient has not read (numbered per nick).\n",
        "[*] /leave <nick> <n>  Recall the Nth unread leave-message or offline file to that nick (aliases: /留言, /unmsg).\n",
        "[*]\n",
        "[*] /clear or /cls  Clear screen (terminal clears; GUI clients clear the current room history).\n",
        "[*] /announce      Show this room's announcement; owner may /announce <text> to set, /announce clear to clear.\n",
        "[*]              Owner: #default is the first user on the server; other rooms, the first /join to that room.\n",
        "[*] /lang [en|zh]   Switch UI language (default English; preference saved per nickname).\n",
        "[*]\n",
        "[*] /game ...      Room games (chess, gomoku, xiangqi, sanguo). /game list /new /join …; owner /game on|off.\n",
        "[*]              See /game help for details. sanguo is available; its UI text may still be Chinese in this release.\n",
        "[*] /news [zh|world|tech|all] [count]  RSS titles and summaries; default 3 per category.\n",
        "[*] /news detail <category> <index>  Longer summary from the RSS item (alias: 详情).\n",
        "[*] /news fetch <category> <index>  Fetch article body from the RSS link (alias: 全文; non-JS sites; may truncate).\n",
        "[*] /library       List library books (epub / txt / md / pdf; per-user bookmarks; page turns auto-save).\n",
        "[*] /lib             Short for /library.\n",
        "[*] /library open <index|filename>  Open a book (resume from bookmark if any); next|prev|page to turn pages.\n",
        "[*] /library find <keyword>        Find by title; while reading, search in the current book (aliases: search / 搜索 / 查找).\n",
        "[*] /dict en|cn|hh <word>  Dictionary: EN→ZH, ZH→EN, Chinese gloss; /dict <word> auto-detects.\n",
        "[*]\n",
        "[*] /sendfile      Send a file to the current room; you get an upload URL; the key is given separately.\n",
        "[*] /sendfile <nick>    Send a file to a user (offline → leave-message on next login; /leave to list/recall).\n",
        "[*] /sendfile #<room>   Send a file to a room; each member gets a distinct download URL + key.\n",
        "[*]              Filename is whatever you upload; you need not put it in the command.\n",
        "[*]              The key is not in the URL—enter it on the page; images, video, PDF can preview in-browser.\n",
        "[*]              Upload and download tokens are single-use; stolen links are useless after use.\n",
        "[*] /canvas        Shared drawing board for the current room (web; alias /board); each person gets a unique URL + key.\n",
        "[*] /canvas <nick>    Private board with an online user.\n",
        "[*] /canvas #<room>   Board for a specific room (you must be in that room).\n",
        "[*] /canvas close     Creator closes the current room board; /canvas new forces a new session.\n",
        "[*]              The key is not in the URL—enter it on the page; strokes sync live. Details: /canvas help.\n",
        "[*] /piano         Shared room piano (web); play with your keyboard—others in the room hear you.\n",
        "[*] /piano <nick>     Private piano with an online user.\n",
        "[*] /piano #<room>    Piano for a specific room (you must be in that room).\n",
        "[*] /piano close      Creator closes the current room piano; /piano new forces a new session.\n",
        "[*]              Details: /piano help.\n",
        "[*]              GUI clients open the board automatically; in a terminal, copy the URL into a browser.\n",
        "[*] /help          Show this help.\n",
    ],
    "game_help_lines": [
        "[*] /game list             List games enabled (online) in this room.",
        "[*] /game new <name>       Start a game in the current room; starter takes seat 1 "
        "(chess: White; gomoku/go/xiangqi/doushou: Black/Black/Red/Red to move; sanguo: host).",
        "[*] /game new <name> ai [easy|normal|hard]  AI practice game (chess/gomoku/xiangqi only); "
        "practice games do not affect persisted ratings.",
        "[*] /game join             Join the game (second seat for chess/gomoku/go/xiangqi/doushou; "
        "sanguo allows 2–6 via join; host starts with /game move 开始).",
        "[*] /game seats            Show players and game state.",
        "[*] /game show             Redraw the board (you at the bottom; opponent view flips automatically).",
        "[*] /game rating [game] [nick]  Show persisted board-game rating/level; ratings are shared across rooms.",
        "[*] Terminal: reversi = /game move <row> <col> (or pass when blocked).",
        "[*] darkchess (Dark Chess / flip chess) EN/ZH: 翻 flip <row> <col> | 走 move <fr> <fc> <tr> <tc>; "
        "4×8 board; first flip assigns red/black; cannon needs one screen; general beats all but soldier, soldier can take general.",
        "[*] Terminal: battleship = place carrier/battleship/cruiser/submarine/destroyer row col h|v, then ready; fire row col. Junqi = setup <piece> row col, then ready and move fr fc tr tc.",
        "[*] chess boards use Unicode pieces (♔♟ etc.); empty squares are ·; last move is marked with parentheses. "
        "Use a monospace font; if Black is hard to see on a dark theme, try a light terminal theme.",
        "[*] /game move …           chess: SAN/UCI; gomoku/go: row col; go may pass; "
        "xiangqi: notation (炮二平五, 马2进3) or four coordinates; "
        "doushou: four coordinates (from-row from-col to-row to-col); "
        "sanguo: Military Struggle rules; while waiting, host starts with 开始; "
        "/game move 武将 lists the general pool; skills like 观星/蛊惑/断粮 — see /game show "
        "(aliases sgs/三国杀). Note: sanguo move help remains largely Chinese in this release.",
        "[*] xiangqi may also be started with alias cchess.",
        "[*] Board marks: + red, - black, ! last move; horse/elephant/advisor advance toward the board midline.",
        "[*] Xiangqi uses contest rules: on threefold repetition, perpetual check/chase without changing loses; "
        "both sides with no legal chase may draw.",
        "[*] /game pgn              Export PGN for the current/finished game (chess only).",
        "[*] /game undo             Undo: side that moved last requests; opponent /game undo accept undoes one ply "
        "(chess/gomoku/go/xiangqi/doushou; short acc / rej / can; reject refuses, cancel withdraws the request).",
        "[*] /game resign           Resign (only while a game is in progress).",
        "[*] /game abort            Abort a game that has not started.",
        "[*] /game end              Room owner may force-end the current game.",
        "[*] /game restore          Restore a game parked by restart/federation into an idle room.",
        "[*] /game on <name>        Owner enables a game in this room (same name aliases as new).",
        "[*] /game off <name>       Owner disables a game in this room (an in-progress match is unaffected).",
        "[*] holdem (Texas Hold'em) EN/ZH command map:",
        "[*]   开始 start | 看牌 look | 过牌 check | 跟注 call | 加注 <amt> raise <amt> | 弃牌 fold | 全下 allin",
        "[*]   bots: bot <easy|hard|pro>; after start, /game show 帮助 shows the full help again.",
        "[*] zjh (Zha Jin Hua) EN/ZH: 开始 start | 看牌 look | 跟注 follow | 加注 raise <amt> | "
        "比牌 compare <nick> | 弃牌 fold; compare costs 2× current bet (×2 again after looking); "
        "ranks: leopard > straight flush > flush > straight > pair > high card; "
        "mixed-suit 235 can beat a leopard; "
        "equal hands: the player who initiated compare loses; next hand deals automatically; "
        "for bots use start bot or bot add [n]; bot <easy|hard|pro> sets difficulty.",
        "[*] mahjong (4 players): if seats are short, start fills with AI; host may bot <easy|hard|pro>.",
        "[*] Supports chi/peng/gang/ron/tsumo; on your turn discard <tile>, or gang/hu; "
        "after another's discard: chi/peng/gang/hu/pass.",
        "[*] Tile codes: m=man (万), p=pin (筒/饼), s=sou (条/索), z=honors (ESWN + dragons).",
        "[*] Chinese discard names work: 二万, 九筒, 五条, 东风, 红中, 发财, 白板 (also m1/p9/s5/z3).",
        "[*] drawguess (Pictionary): need 2+ players; host start; drawer uses /canvas; "
        "others /game move guess <word>; skip; drawer may word to re-read the secret.",
    ],
    "server": {
        "announce_preview": "[#{room}] [*] Announcement: {text}\n",
        "announce_current": "[*] #{room} current announcement: {text}\n",
        "announce_none": "[*] #{room} has no announcement.\n",
        "announce_owner_only": "[*] Only the room owner can change the announcement (viewing needs no permission).\n",
        "announce_cleared_bcast": "[#{room}] [*] Announcement cleared.\n",
        "announce_cleared": "[*] Cleared the announcement for #{room}.\n",
        "announce_too_long": "[*] Announcement too long (max {max_len} characters).\n",
        "announce_updated": "[*] Updated the announcement for #{room}.\n",
        "announce_set_bcast": "[#{room}] [*] Announcement: {text}\n",
        "offline_header": "[*] You have {n} leave-message(s) (received while offline, oldest first):\n",
        "offline_file_meta": "[*] (offline file {when}, from {sender})\n",
        "offline_file_pm": "[PM from {sender}] (offline file {when}) {text}\n",
        "offline_pm": "[PM from {sender}] (leave-message {when}) {text}\n",
        "leave_none": "[*] You have no unread leave-messages or files awaiting the recipient.\n",
        "leave_list_header": "[*] Unread leave-messages/files you sent ({n} total):\n",
        "leave_recall_hint": "[*] Recall: /leave {recipient} <n>\n",
        "leave_group": "[*] → {name}:\n",
        "leave_item": "[*]   {index}. ({when}) {text}\n",
        "leave_usage": (
            "[*] Usage: /leave [nick]  |  /leave <nick> <n>\n"
            "[*] (list or recall leave-messages/offline files you sent that are still unread)\n"
        ),
        "leave_bad_index": "[*] Index must be a positive integer.\n",
        "leave_recalled": (
            "[*] Recalled {kind} #{index} sent to {recipient}"
            " ({when}): {text}\n"
        ),
        "leave_recall_fail": (
            "[*] Recall failed: no unread leave-message/file #{index} to {recipient}"
            " (try /leave {recipient}).\n"
        ),
        "fed_connected": (
            "[*] Federation connected to {n} node(s) (same nick/rooms merge across servers).\n"
        ),
        "multi_terminal": (
            "[*] Another terminal for this account is online; rooms synced and resume-play is available.\n"
        ),
        "session_restored": (
            "[*] Restored your last client session and rooms; unfinished games can continue.\n"
        ),
        "game_seat_resumed": "[*] Took over game seat(s) held by a previous connection.\n",
        "game_resume_takeover": "Auto-resumed and took over the old terminal's seat.",
        "game_usage_header": "[*] /game usage:\n",
        "game_room_playable": "[*] Playable in this room: {games}\n",
        "game_list_line": (
            "[*] Playable games: {games}"
            " (xiangqi alias cchess; sanguo aliases sgs/三国杀)\n"
        ),
        "game_list_empty": (
            "[*] No games enabled in this room; owner can /game on <name>.\n"
        ),
        "game_forward_fail": "[*] Cannot reach the node hosting this game; try again later.\n",
        "game_cmd_fail": (
            "[*] /game failed; try again later (see server log for details).\n"
        ),
        "news_cmd_fail": (
            "[*] News command failed; try again later (see server log for details).\n"
        ),
        "library_cmd_fail": (
            "[*] Library command failed; try again later (see server log for details).\n"
        ),
        "lang_usage": "[*] Usage: /lang [en|zh]\n",
        "lang_set": "[*] UI language set to {lang}.\n",
        "lang_current": "[*] Current UI language: {lang}\n",
        "rating_no_persist": "[*] {game} has no persisted board-game rating.\n",
    },
}
