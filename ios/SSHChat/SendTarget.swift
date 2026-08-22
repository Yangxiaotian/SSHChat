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
}
