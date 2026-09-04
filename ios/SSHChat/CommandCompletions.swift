import Foundation

enum CommandUsage {
    private static let storageKey = "sshchat.command-usage:v1"

    private static var counts: [String: Int] {
        get { UserDefaults.standard.dictionary(forKey: storageKey) as? [String: Int] ?? [:] }
        set { UserDefaults.standard.set(newValue, forKey: storageKey) }
    }

    static func record(_ text: String) {
        guard text.hasPrefix("/") else { return }
        let parts = text.trimmingCharacters(in: .whitespacesAndNewlines)
            .split(whereSeparator: \.isWhitespace)
            .map { String($0).lowercased() }
            .filter { !$0.isEmpty }
        guard !parts.isEmpty else { return }
        var c = counts
        var path = ""
        for (i, part) in parts.enumerated() {
            path = i == 0 ? part : "\(path) \(part)"
            c[path, default: 0] += 1
        }
        counts = c
    }

    static func count(for key: String) -> Int {
        counts[key.lowercased()] ?? 0
    }

    static func sort(_ items: [String], defaultOrder: [String]) -> [String] {
        items.sorted { a, b in
            let ca = count(for: a)
            let cb = count(for: b)
            if ca != cb { return ca > cb }
            let ia = defaultOrder.firstIndex(of: a) ?? Int.max
            let ib = defaultOrder.firstIndex(of: b) ?? Int.max
            return ia < ib
        }
    }
}

enum CommandCompletions {
    private static let top = [
        "/help", "/lang", "/language", "/names", "/users", "/rooms",
        "/join", "/switch", "/part", "/msg", "/sendfile", "/file",
        "/canvas", "/board", "/piano", "/leave", "/unmsg", "/announce", "/poll", "/later",
        "/game", "/news", "/library", "/lib", "/dict", "/clear", "/cls", "/dnd",
    ]

    private static let subs: [String: [String]] = [
        "/game": [
            "help", "list", "new", "join", "show", "move", "resign",
            "undo", "abort", "end", "on", "off", "seats", "rating", "pgn",
        ],
        "/news": ["中文", "国际", "科技", "all", "detail", "详情", "fetch", "全文"],
        "/library": [
            "open", "read", "next", "n", "prev", "p", "page", "find", "search",
            "bookmarks", "bookmark", "reset", "close", "info", "show", "help",
        ],
        "/lib": [
            "open", "read", "next", "n", "prev", "p", "page", "find", "search",
            "bookmarks", "bookmark", "reset", "close", "info", "show", "help",
        ],
        "/dict": ["en", "cn", "hh", "help", "英", "中", "汉"],
        "/dnd": ["on", "off"],
        "/lang": ["en", "zh", "english", "chinese", "中文", "英文"],
        "/language": ["en", "zh", "english", "chinese", "中文", "英文"],
        "/poll": ["new", "close", "help", "show"],
        "/later": ["list", "ls", "show", "cancel", "help"],
    ]

    private static let nested: [String: [String]] = [
        "/game undo": ["accept", "reject", "cancel"],
    ]

    private static let roomArgCmds: Set<String> = ["/join", "/switch", "/part"]
    private static let userOrRoomArgCmds: Set<String> = ["/msg", "/sendfile", "/file"]
    private static let userArgCmds: Set<String> = ["/leave", "/unmsg"]

    private static func sorted(_ items: [String], defaultOrder: [String]) -> [String] {
        guard !items.isEmpty else { return items }
        return CommandUsage.sort(items, defaultOrder: defaultOrder)
    }

    private static func uniq(_ items: [String]) -> [String] {
        var seen = Set<String>()
        var out: [String] = []
        for raw in items {
            let key = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !key.isEmpty else { continue }
            let low = key.lowercased()
            guard seen.insert(low).inserted else { continue }
            out.append(key)
        }
        return out
    }

    static func nameArgCompletions(
        _ text: String,
        rooms: [String],
        users: [String]
    ) -> [String] {
        guard text.hasPrefix("/") else { return [] }
        let trailingSpace = text.hasSuffix(" ")
        let parts = text.trimmingCharacters(in: .whitespaces)
            .split(whereSeparator: \.isWhitespace)
            .map(String.init)
            .filter { !$0.isEmpty }
        guard let cmdRaw = parts.first else { return [] }
        let cmd = cmdRaw.lowercased()
        let roomNames = uniq(rooms.map { $0.trimmingCharacters(in: CharacterSet(charactersIn: "#")) })
        let userNames = uniq(users)

        let cands: [String]
        if roomArgCmds.contains(cmd) {
            cands = roomNames
        } else if userOrRoomArgCmds.contains(cmd) {
            cands = userNames + roomNames.map { "#\($0)" }
        } else if userArgCmds.contains(cmd) {
            cands = userNames
        } else {
            return []
        }

        if trailingSpace && parts.count == 1 {
            return cands.map { "\(parts[0]) \($0)" }
        }
        if parts.count >= 2 && !trailingSpace {
            let prefix = parts[1]
            let pl = prefix.lowercased()
            let bare = pl.hasPrefix("#") ? String(pl.dropFirst()) : pl
            let matched = cands.filter { c in
                let cl = c.lowercased()
                if pl == "#" { return c.hasPrefix("#") }
                if cl.hasPrefix(pl) { return true }
                if c.hasPrefix("#"), String(c.dropFirst()).lowercased().hasPrefix(bare) { return true }
                if !c.hasPrefix("#"), cl.hasPrefix(bare), prefix.hasPrefix("#") { return true }
                return false
            }
            return matched.map { "\(parts[0]) \($0)" }
        }
        return []
    }

    static func completions(
        _ text: String,
        rooms: [String] = [],
        users: [String] = []
    ) -> [String] {
        guard text.hasPrefix("/") else { return [] }
        if !text.contains(" ") {
            return sorted(top.filter { $0.hasPrefix(text) }, defaultOrder: top)
        }

        let trailingSpace = text.hasSuffix(" ")
        let parts = text.trimmingCharacters(in: .whitespaces)
            .split(whereSeparator: \.isWhitespace)
            .map(String.init)
            .filter { !$0.isEmpty }
        guard !parts.isEmpty else { return [] }
        let cmd = parts[0].lowercased()

        if parts.count == 1 && !trailingSpace {
            return sorted(top.filter { $0.hasPrefix(parts[0]) }, defaultOrder: top)
        }

        if parts.count >= 2 {
            let sub = parts[1].lowercased()
            let nestedItems = nested["\(cmd) \(sub)"] ?? []
            if !nestedItems.isEmpty {
                let nestedFull = nestedItems.map { "\(parts[0]) \(parts[1]) \($0)" }
                if trailingSpace && parts.count == 2 {
                    return sorted(nestedFull, defaultOrder: nestedFull)
                }
                if parts.count >= 3 && !trailingSpace {
                    let prefix = parts[2]
                    return sorted(
                        nestedItems
                            .filter { $0.hasPrefix(prefix) }
                            .map { "\(parts[0]) \(parts[1]) \($0)" },
                        defaultOrder: nestedFull
                    )
                }
                if !(parts.count == 2 && !trailingSpace) {
                    return []
                }
            }
        }

        let list = subs[cmd] ?? []
        if !list.isEmpty {
            let subsFull = list.map { "\(parts[0]) \($0)" }
            if trailingSpace && parts.count == 1 {
                return sorted(subsFull, defaultOrder: subsFull)
            }
            if parts.count >= 2 && !trailingSpace {
                let prefix = parts[1]
                return sorted(
                    list.filter { $0.hasPrefix(prefix) }.map { "\(parts[0]) \($0)" },
                    defaultOrder: subsFull
                )
            }
            return []
        }

        let nameItems = nameArgCompletions(text, rooms: rooms, users: users)
        return sorted(nameItems, defaultOrder: nameItems)
    }

    static func applyTab(
        _ text: String,
        rooms: [String] = [],
        users: [String] = []
    ) -> String? {
        guard text.hasPrefix("/") else { return nil }
        let items = completions(text, rooms: rooms, users: users)
        if items.isEmpty { return nil }
        if items.count == 1 {
            let one = items[0]
            return one.hasSuffix(" ") ? one : "\(one) "
        }
        let shared = longestCommonPrefix(items)
        return shared.count > text.count ? shared : nil
    }

    private static func longestCommonPrefix(_ values: [String]) -> String {
        guard var prefix = values.first else { return "" }
        for v in values.dropFirst() {
            while !v.hasPrefix(prefix) {
                prefix = String(prefix.dropLast())
                if prefix.isEmpty { return "" }
            }
        }
        return prefix
    }
}
