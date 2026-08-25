import Foundation

enum CommandCompletions {
    private static let top = [
        "/help", "/lang", "/language", "/names", "/users", "/rooms",
        "/join", "/switch", "/part", "/msg", "/sendfile", "/file",
        "/canvas", "/board", "/leave", "/unmsg", "/announce", "/game",
        "/news", "/library", "/lib", "/dict", "/clear", "/cls", "/dnd",
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
    ]

    private static let nested: [String: [String]] = [
        "/game undo": ["accept", "reject", "cancel"],
    ]

    static func completions(_ text: String) -> [String] {
        guard text.hasPrefix("/") else { return [] }
        if !text.contains(" ") {
            return top.filter { $0.hasPrefix(text) }
        }

        let trailingSpace = text.hasSuffix(" ")
        let parts = text.trimmingCharacters(in: .whitespaces)
            .split(whereSeparator: \.isWhitespace)
            .map(String.init)
            .filter { !$0.isEmpty }
        guard !parts.isEmpty else { return [] }
        let cmd = parts[0].lowercased()

        if parts.count == 1 && !trailingSpace {
            return top.filter { $0.hasPrefix(parts[0]) }
        }

        if parts.count >= 2 {
            let sub = parts[1].lowercased()
            let nestedItems = nested["\(cmd) \(sub)"] ?? []
            if !nestedItems.isEmpty {
                if trailingSpace && parts.count == 2 {
                    return nestedItems.map { "\(parts[0]) \(parts[1]) \($0)" }
                }
                if parts.count >= 3 && !trailingSpace {
                    let prefix = parts[2]
                    return nestedItems
                        .filter { $0.hasPrefix(prefix) }
                        .map { "\(parts[0]) \(parts[1]) \($0)" }
                }
                if !(parts.count == 2 && !trailingSpace) {
                    return []
                }
            }
        }

        let list = subs[cmd] ?? []
        if list.isEmpty { return [] }
        if trailingSpace && parts.count == 1 {
            return list.map { "\(parts[0]) \($0)" }
        }
        if parts.count >= 2 && !trailingSpace {
            let prefix = parts[1]
            return list.filter { $0.hasPrefix(prefix) }.map { "\(parts[0]) \($0)" }
        }
        return []
    }

    static func applyTab(_ text: String) -> String? {
        guard text.hasPrefix("/") else { return nil }
        let items = completions(text)
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
