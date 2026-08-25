import Foundation

enum SendTarget: Equatable {
    case currentRoom(String)
    case user(String)
    case namedRoom(String)

    var label: String {
        switch self {
        case .currentRoom(let room): return "当前房间 #\(room)"
        case .user(let nick): return "私聊 \(nick)"
        case .namedRoom(let room): return "房间 #\(room)"
        }
    }

    var sendfileCommand: String {
        switch self {
        case .currentRoom: return "/sendfile"
        case .user(let nick): return "/sendfile \(nick)"
        case .namedRoom(let room): return "/sendfile #\(room)"
        }
    }

    static func outboundText(target: SendTarget, draft: String) -> String {
        let t = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        if t.hasPrefix("/") { return draft }
        switch target {
        case .currentRoom: return draft
        case .user(let nick): return "/msg \(nick) \(draft)"
        case .namedRoom(let room): return "/msg #\(room) \(draft)"
        }
    }
}

enum SendTargetStore {
    private static let kindKey = "send_target_kind"
    private static let valueKey = "send_target_value"
    private static let recentKey = "send_target_recent"
    private static let roomKey = "current_room"

    static func loadCurrentRoom() -> String {
        let r = UserDefaults.standard.string(forKey: roomKey)?.trimmingCharacters(in: .whitespacesAndNewlines)
        return (r?.isEmpty == false) ? r! : "default"
    }

    static func saveCurrentRoom(_ room: String) {
        UserDefaults.standard.set(room.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "default" : room, forKey: roomKey)
    }

    static func loadTarget() -> SendTarget {
        let room = loadCurrentRoom()
        switch UserDefaults.standard.string(forKey: kindKey) {
        case "user":
            let nick = UserDefaults.standard.string(forKey: valueKey)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            return nick.isEmpty ? .currentRoom(room) : .user(nick)
        case "named_room":
            let r = UserDefaults.standard.string(forKey: valueKey)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            return r.isEmpty ? .currentRoom(room) : .namedRoom(r)
        default:
            return .currentRoom(room)
        }
    }

    static func saveTarget(_ target: SendTarget) {
        switch target {
        case .currentRoom(let room):
            UserDefaults.standard.set("room", forKey: kindKey)
            UserDefaults.standard.set(room, forKey: valueKey)
            saveCurrentRoom(room)
        case .user(let nick):
            UserDefaults.standard.set("user", forKey: kindKey)
            UserDefaults.standard.set(nick, forKey: valueKey)
            rememberRecent(nick)
        case .namedRoom(let room):
            UserDefaults.standard.set("named_room", forKey: kindKey)
            UserDefaults.standard.set(room, forKey: valueKey)
        }
    }

    static func loadRecentUsers() -> [String] {
        UserDefaults.standard.stringArray(forKey: recentKey) ?? []
    }

    static func rememberRecent(_ nick: String) {
        let key = nick.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !key.isEmpty else { return }
        var merged = [key] + loadRecentUsers().filter { $0.caseInsensitiveCompare(key) != .orderedSame }
        merged = Array(merged.prefix(8))
        UserDefaults.standard.set(merged, forKey: recentKey)
    }
}

enum ChatLineParsers {
    struct NamesLine {
        let room: String
        let members: [String]
    }

    struct PmLine {
        let from: String
        let body: String
    }

    private static let activeRoom = try! NSRegularExpression(
        pattern: #"Active room #([A-Za-z0-9_-]+)"#, options: [.caseInsensitive]
    )
    private static let switchedTo = try! NSRegularExpression(
        pattern: #"Switched from #\S+ to #([A-Za-z0-9_-]+)"#, options: [.caseInsensitive]
    )
    private static let namesLine = try! NSRegularExpression(
        pattern: #"^\[\*]\s+#([^\s(]+)\s+\(\d+\):\s*(.*)$"#, options: [.caseInsensitive]
    )
    private static let pmLine = try! NSRegularExpression(
        pattern: #"^\[PM from ([^\]]+)]\s*(.*)$"#, options: [.caseInsensitive]
    )

    static func parseActiveRoom(_ line: String) -> String? {
        let t = line.trimmingCharacters(in: .whitespacesAndNewlines)
        let range = NSRange(t.startIndex..., in: t)
        if let m = activeRoom.firstMatch(in: t, range: range), let r = Range(m.range(at: 1), in: t) {
            return String(t[r])
        }
        if let m = switchedTo.firstMatch(in: t, range: range), let r = Range(m.range(at: 1), in: t) {
            return String(t[r])
        }
        return nil
    }

    static func parseNames(_ line: String) -> NamesLine? {
        let t = line.trimmingCharacters(in: .whitespacesAndNewlines)
        let range = NSRange(t.startIndex..., in: t)
        guard let m = namesLine.firstMatch(in: t, range: range),
              let roomR = Range(m.range(at: 1), in: t),
              let tailR = Range(m.range(at: 2), in: t)
        else { return nil }
        let room = String(t[roomR])
        let tail = String(t[tailR]).trimmingCharacters(in: .whitespacesAndNewlines)
        if tail.isEmpty || tail.caseInsensitiveCompare("(empty)") == .orderedSame {
            return NamesLine(room: room, members: [])
        }
        let members = tail.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        return NamesLine(room: room, members: members)
    }

    static func parsePm(_ line: String) -> PmLine? {
        let t = line.trimmingCharacters(in: .whitespacesAndNewlines)
        let range = NSRange(t.startIndex..., in: t)
        guard let m = pmLine.firstMatch(in: t, range: range),
              let fromR = Range(m.range(at: 1), in: t),
              let bodyR = Range(m.range(at: 2), in: t)
        else { return nil }
        return PmLine(from: String(t[fromR]), body: String(t[bodyR]))
    }

    // Same shapes as client.py / electron (do not treat [#room] or [HH:MM:SS] as sender).
    // Body separator is \\s+ so odd PTY spacing still matches.
    private static let roomChat = try! NSRegularExpression(
        pattern: #"^\[#([^\]]+)]\s+\[([^\]]+)]\s+(.*)$"#
    )
    private static let plainChat = try! NSRegularExpression(
        pattern: #"^\[([^\]]+)]\s+(.*)$"#
    )
    /// Non-anchored: last `[#room] [nick] body` in a noisy PTY line.
    private static let roomChatLoose = try! NSRegularExpression(
        pattern: #"\[#([^\]]+)]\s+\[([^\]]+)]\s+(.*)$"#
    )
    private static let timePrefix = try! NSRegularExpression(
        pattern: #"^(?:>?\s*)?(?:\[\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?]|(\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?))\s+"#
    )
    private static let leadingGarbageScalars = CharacterSet(charactersIn: "\u{FFFD}\u{25A1}\u{FEFF}\u{00A0}")
        .union(.whitespacesAndNewlines)
    private static let systemSenders: Set<String> = ["+", "-", "*", "!"]
    /// Non-user bracket tags that must never chime (e.g. command acks).
    private static let ignoredSenders: Set<String> = [
        "OK", "ERROR", "INFO", "WARN", "WARNING", "DEBUG", "HINT",
    ]

    /// Strip local clock / prompt prefixes before parsing chat.
    static func normalizeForParse(_ line: String) -> String {
        var t = line.trimmingCharacters(in: .whitespacesAndNewlines)
        while let first = t.unicodeScalars.first, leadingGarbageScalars.contains(first) {
            t = String(t.unicodeScalars.dropFirst())
        }
        while true {
            let range = NSRange(t.startIndex..., in: t)
            guard let m = timePrefix.firstMatch(in: t, range: range),
                  let r = Range(m.range, in: t)
            else { break }
            t = String(t[r.upperBound...]).trimmingCharacters(in: .whitespaces)
        }
        if t.hasPrefix(">") {
            t = String(t.drop(while: { $0 == ">" || $0 == " " }))
        }
        return t
    }

    struct ChatLine {
        let room: String?
        let sender: String
        let body: String
    }

    static func parseChat(_ line: String) -> ChatLine? {
        let t = normalizeForParse(line)
        let range = NSRange(t.startIndex..., in: t)
        if let m = roomChat.firstMatch(in: t, range: range),
           let roomR = Range(m.range(at: 1), in: t),
           let senderR = Range(m.range(at: 2), in: t),
           let bodyR = Range(m.range(at: 3), in: t)
        {
            return ChatLine(room: String(t[roomR]), sender: String(t[senderR]), body: String(t[bodyR]))
        }
        if let m = plainChat.firstMatch(in: t, range: range),
           let senderR = Range(m.range(at: 1), in: t),
           let bodyR = Range(m.range(at: 2), in: t)
        {
            let sender = String(t[senderR])
            // Avoid treating "[PM from x] …" as a plain chat sender.
            if sender.lowercased().hasPrefix("pm from ") { return nil }
            return ChatLine(room: nil, sender: sender, body: String(t[bodyR]))
        }
        // PTY sometimes leaves junk before the real chat brackets — take the last pair.
        if let m = roomChatLoose.firstMatch(in: t, range: range),
           let roomR = Range(m.range(at: 1), in: t),
           let senderR = Range(m.range(at: 2), in: t),
           let bodyR = Range(m.range(at: 3), in: t)
        {
            return ChatLine(room: String(t[roomR]), sender: String(t[senderR]), body: String(t[bodyR]))
        }
        return nil
    }

    /// Whether an incoming line should trigger a receive chime (peer / PM / join-leave).
    static func shouldAlert(
        _ line: String,
        myName: String,
        recentOutboundBody: String = "",
        recentOutboundAt: TimeInterval = 0
    ) -> Bool {
        let t = normalizeForParse(line)
        if t.isEmpty { return false }
        if parsePm(t) != nil { return true }

        guard let chat = parseChat(t) else {
            let lower = t.lowercased()
            return lower.contains(" joined ") || lower.contains(" left ")
        }
        var sender = chat.sender
        var body = chat.body
        // If a leftover clock was parsed as sender, the real chat is in the body.
        if sender.range(of: #"^\d{1,2}:\d{2}"#, options: .regularExpression) != nil {
            guard let nested = parseChat(body) else { return false }
            sender = nested.sender
            body = nested.body
        }
        // If `[#room]` was eaten as plain sender (spacing quirk), body is `[nick] text`.
        if sender.hasPrefix("#") {
            guard let nested = parseChat(body) else { return false }
            sender = nested.sender
            body = nested.body
        }
        if ignoredSenders.contains(sender.uppercased()) { return false }
        if systemSenders.contains(sender) {
            if sender == "+" || sender == "-" { return true }
            if sender == "!" {
                let lower = body.lowercased()
                return lower.contains(" joined ") || lower.contains(" left ")
            }
            return false
        }
        let me = myName.trimmingCharacters(in: .whitespacesAndNewlines)
        if !me.isEmpty, sender.caseInsensitiveCompare(me) == .orderedSame {
            return false
        }
        // Suppress exact echo of what we just sent (do NOT use hasSuffix — short Chinese
        // words like "好" would suppress peer "你好").
        let recent = recentOutboundBody.trimmingCharacters(in: .whitespacesAndNewlines)
        if !recent.isEmpty,
           Date().timeIntervalSince1970 - recentOutboundAt < 4,
           body == recent
        {
            return false
        }
        return true
    }

    /// UI classification for bubble / system / board cards.
    enum DisplayKind {
        case bubble(mine: Bool, room: String?, sender: String, body: String, time: String)
        case system(String)
        case boardLine(String)
    }

    private static let clockCapture = try! NSRegularExpression(
        pattern: #"^>?\[?(\d{1,2}:\d{2}(?::\d{2})?)\]?\s+"#
    )

    /// Prefer line clock (`[HH:MM:SS]`); else local now.
    static func extractDisplayTime(_ line: String) -> String {
        let t = line.trimmingCharacters(in: .whitespacesAndNewlines)
        let range = NSRange(t.startIndex..., in: t)
        if let m = clockCapture.firstMatch(in: t, range: range),
           let r = Range(m.range(at: 1), in: t)
        {
            return String(t[r])
        }
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "HH:mm:ss"
        return f.string(from: Date())
    }

    /// `[#room] [*] body` (server) or `[*] body` (client.py SSH display).
    private static let gameStarRoom = try! NSRegularExpression(
        pattern: #"^\[#[^\]]+\]\s+\[\*\](?: (.*))?$"#
    )
    private static let gameStarBare = try! NSRegularExpression(
        pattern: #"^\[\*\](?: (.*))?$"#
    )

    /// Body only (leading spaces kept). Nil if not a game/system-star wire line.
    /// Mobile SSH sessions run client.py, which rewrites `[#room] [*]` → `[*]`.
    static func parseGameStarBody(_ line: String) -> String? {
        let t = normalizeForParse(line)
        let range = NSRange(t.startIndex..., in: t)
        if let m = gameStarRoom.firstMatch(in: t, range: range) {
            if m.range(at: 1).location == NSNotFound { return "" }
            guard let bodyR = Range(m.range(at: 1), in: t) else { return "" }
            return String(t[bodyR])
        }
        if let m = gameStarBare.firstMatch(in: t, range: range) {
            if m.range(at: 1).location == NSNotFound { return "" }
            guard let bodyR = Range(m.range(at: 1), in: t) else { return "" }
            return String(t[bodyR])
        }
        return nil
    }

    static func shouldContinueBoard(_ line: String) -> Bool {
        if parseGameStarBody(line) != nil { return true }
        if let chat = parseChat(line), systemSenders.contains(chat.sender) { return true }
        if looksLikeGameBoardContent(line) { return true }
        if let chat = parseChat(line), looksLikeGameBoardContent(chat.body) { return true }
        return false
    }

    /// Board / game ASCII — must never become a WeChat bubble or centered system tip.
    static func looksLikeGameBoardContent(_ payload: String) -> Bool {
        let t = payload.trimmingCharacters(in: .whitespacesAndNewlines)
        if t.isEmpty { return false }
        let chessPieces = CharacterSet(charactersIn: "♔♕♖♗♘♙♚♛♜♝♞♟")
        if t.unicodeScalars.contains(where: { chessPieces.contains($0) }) { return true }
        if t.contains("楚河汉界") || t.contains("图例：") || t.contains("请用等宽") || t.contains("己方在下方") {
            return true
        }
        if t.contains("←") && (t.contains("纵线") || t.contains("红方") || t.contains("黑方") || t.contains("白方")) {
            return true
        }
        if t.contains("-车") || t.contains("+车") || t.contains("-将") || t.contains("+帅")
            || t.contains("-马") || t.contains("+马")
        {
            return true
        }
        if t.range(of: #"^[+\-!·]"#, options: .regularExpression) != nil, t.count > 6 { return true }
        if t.range(of: #"^\d{1,2}\s+(?:[.#o●○·]\s*){4,}"#, options: .regularExpression) != nil {
            return true
        }
        if t.range(of: #"^[a-h](?:\s+[a-h]){7}\s*$"#, options: [.regularExpression, .caseInsensitive]) != nil {
            return true
        }
        if t.range(of: #"^(?:\d{1,2}\s+){7,}\d{1,2}\s*$"#, options: .regularExpression) != nil {
            return true
        }
        if t.range(of: #"^[一二三四五六七八九](?:\s+[一二三四五六七八九]){3,}"#, options: .regularExpression) != nil {
            return true
        }
        let keys = [
            "轮到", "上一步", "对局", "gomoku", "chess", "xiangqi", "围棋",
            "五子棋", "中国象棋", "国际象棋", "斗兽棋", "积分=", "rating=", "W/L/D",
            "将军", "停一手", "落子", "走子", "行棋", "空席",
        ]
        let lower = t.lowercased()
        if keys.contains(where: { t.contains($0) || lower.contains($0.lowercased()) }) { return true }
        let dots = t.filter { $0 == "·" || $0 == "." }.count
        if dots >= 8 && t.count < 140 { return true }
        let goish = t.filter { $0 == "#" || $0 == "o" || $0 == "O" }.count
        if goish >= 5 && dots >= 5 { return true }
        return false
    }

    static func classifyForDisplay(_ line: String, myName: String) -> DisplayKind {
        if let body = parseGameStarBody(line) {
            return .boardLine(body)
        }
        if let chat = parseChat(line) {
            var sender = chat.sender
            var body = chat.body
            var room = chat.room
            if sender.range(of: #"^\d{1,2}:\d{2}"#, options: .regularExpression) != nil,
               let nested = parseChat(body)
            {
                sender = nested.sender
                body = nested.body
                room = nested.room ?? room
            }
            if sender.hasPrefix("#"), let nested = parseChat(body) {
                sender = nested.sender
                body = nested.body
                room = nested.room ?? room
            }
            // Game boards arrive as [*]; join/leave [+]/[!]/[-] stay gray system (not bubbles).
            if sender == "*" {
                return .boardLine(body)
            }
            if systemSenders.contains(sender) || ignoredSenders.contains(sender.uppercased()) {
                return .system(body.isEmpty ? line : "[\(sender)] \(body)")
            }
            // Real user chat → always bubble (never board heuristics).
            let me = myName.trimmingCharacters(in: .whitespacesAndNewlines)
            let mine = !me.isEmpty && sender.caseInsensitiveCompare(me) == .orderedSame
            return .bubble(mine: mine, room: room, sender: sender, body: body, time: extractDisplayTime(line))
        }
        if looksLikeGameBoardContent(line) {
            return .boardLine(line.trimmingCharacters(in: .newlines))
        }
        return .system(line)
    }
}
