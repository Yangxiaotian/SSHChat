import Foundation

struct LibraryBook: Identifiable, Equatable {
    var id: Int { index }
    let index: Int
    let format: String
    let name: String
    let size: String
    let origin: String?
    /// 1-based page from `· 书签第 N 页`.
    let bookmarkPage: Int?

    var openToken: String {
        if let origin, !origin.isEmpty { return "\(name)@\(origin)" }
        return name
    }

    var displayTitle: String {
        if let dot = name.lastIndex(of: "."), name.distance(from: name.startIndex, to: dot) > 0 {
            return String(name[..<dot])
        }
        return name
    }
}

struct LibraryPage: Equatable {
    let title: String
    /// 1-based.
    let page: Int
    let total: Int
    let paragraphs: [String]
}

struct LibrarySearchHit: Identifiable, Equatable {
    var id: String { "\(page)-\(snippet)" }
    let page: Int
    let snippet: String
}

enum LibraryEvent: Equatable {
    case catalogStart
    case catalog(books: [LibraryBook], notes: [String])
    case pageStart(title: String, page: Int, total: Int)
    case page(LibraryPage)
    case searchResults(title: String, query: String, hits: [LibrarySearchHit])
    case notice(String)
    case loading(String)
    case error(String)
    case closed
    case needsReopen
}

/// Turns server `/library` star lines (body after `[*]`) into structured events.
final class LibraryParser {
    struct Result {
        let libraryLine: Bool
        let events: [LibraryEvent]
        static let none = Result(libraryLine: false, events: [])
    }

    private enum Section { case none, catalog, page, search }

    private var section: Section = .none
    private var books: [LibraryBook] = []
    private var notes: [String] = []
    private var catalogDirty = false
    private var pageTitle = ""
    private var pageNo = 0
    private var pageTotal = 0
    private var pageLines: [String] = []
    private var searchTitle = ""
    private var searchQuery = ""
    private var hits: [LibrarySearchHit] = []

    private static let pageIndent = "   "
    static let defaultWrapBytes = 78

    private static let catalogHeader = try! NSRegularExpression(pattern: #"^---\s*图书馆\s*---$"#)
    private static let pageHeader = try! NSRegularExpression(
        pattern: #"^---\s*《(.+)》\s*第\s*(\d+)\s*/\s*(\d+)\s*页\s*---$"#
    )
    private static let bookLine = try! NSRegularExpression(
        pattern: #"^(\d+)\.\s+\[([A-Za-z0-9]+)\]\s+(.+?)\s+\(([\d.]+\s*[KMGT]?B)\)(?:\s+@(\S+))?(?:\s+·\s+书签第\s*(\d+)\s*页)?$"#
    )
    private static let searchHeader = try! NSRegularExpression(
        pattern: #"^在《(.+)》中搜索「(.+)」，找到\s*\d+\s*处：$"#
    )
    private static let searchNone = try! NSRegularExpression(
        pattern: #"^在《(.+)》中未找到「(.+)」。$"#
    )
    private static let searchHit = try! NSRegularExpression(pattern: #"^第\s*(\d+)\s*页：(.*)$"#)

    private static let catalogNotePrefixes = [
        "联邦并集共", "查找「", "未找到匹配的图书", "用 /library 查看全部书目",
        "本机图书馆目录不存在", "本机目录为空", "联邦暂无共享图书",
    ]
    private static let catalogTerminalPrefixes = ["用 /library 查看全部书目", "联邦暂无共享图书"]
    private static let errorPrefixes = [
        "无法读取图书", "无法读取当前图书", "打开失败", "检索失败", "跳转失败",
        "未找到图书：", "无效页码", "页码须为整数",
    ]

    func reset() {
        section = .none
        books.removeAll()
        notes.removeAll()
        catalogDirty = false
        pageLines.removeAll()
        hits.removeAll()
    }

    /// Show catalog rows received so far after a quiet period.
    func flush() -> [LibraryEvent] {
        guard section == .catalog, catalogDirty else { return [] }
        catalogDirty = false
        return [.catalog(books: books, notes: notes)]
    }

    func feed(_ body: String) -> Result {
        let t = body.trimmingCharacters(in: .whitespacesAndNewlines)
        var pre: [LibraryEvent] = []

        switch section {
        case .page:
            if t.hasPrefix("翻页：") || t.hasPrefix("翻页:") {
                section = .none
                return Result(libraryLine: true, events: [pageEvent()])
            }
            if body.hasPrefix("  "), !t.isEmpty {
                let line = body.hasPrefix(Self.pageIndent)
                    ? String(body.dropFirst(Self.pageIndent.count))
                    : body
                pageLines.append(line)
                return Result(libraryLine: true, events: [])
            }
            pre.append(pageEvent())
            section = .none
        case .catalog:
            if let book = Self.parseBook(t) {
                books.append(book)
                catalogDirty = true
                return Result(libraryLine: true, events: [])
            }
            if Self.catalogNotePrefixes.contains(where: { t.hasPrefix($0) }) {
                notes.append(t)
                catalogDirty = true
                if Self.catalogTerminalPrefixes.contains(where: { t.hasPrefix($0) }) {
                    section = .none
                    catalogDirty = false
                    return Result(libraryLine: true, events: [.catalog(books: books, notes: notes)])
                }
                return Result(libraryLine: true, events: [])
            }
            if t.hasPrefix("打开：/library") || t.hasPrefix("查找：/library") {
                return Result(libraryLine: true, events: [])
            }
            if t.hasPrefix("我的书签：/library") {
                section = .none
                catalogDirty = false
                return Result(libraryLine: true, events: [.catalog(books: books, notes: notes)])
            }
            if catalogDirty { pre.append(.catalog(books: books, notes: notes)) }
            catalogDirty = false
            section = .none
        case .search:
            if let m = Self.match(Self.searchHit, t) {
                let page = Int(m[0]) ?? 0
                hits.append(LibrarySearchHit(page: page, snippet: m[1].trimmingCharacters(in: .whitespaces)))
                return Result(libraryLine: true, events: [])
            }
            if t.hasPrefix("用 /library page") {
                section = .none
                return Result(libraryLine: true, events: [searchEvent()])
            }
            pre.append(searchEvent())
            section = .none
        case .none:
            break
        }

        let r = feedIdle(t)
        if pre.isEmpty { return r }
        return Result(libraryLine: r.libraryLine, events: pre + r.events)
    }

    private func feedIdle(_ t: String) -> Result {
        if Self.matches(Self.catalogHeader, t) {
            section = .catalog
            books.removeAll()
            notes.removeAll()
            catalogDirty = false
            return Result(libraryLine: true, events: [.catalogStart])
        }
        if let m = Self.match(Self.pageHeader, t) {
            section = .page
            pageTitle = m[0]
            pageNo = Int(m[1]) ?? 1
            pageTotal = Int(m[2]) ?? 1
            pageLines.removeAll()
            return Result(libraryLine: true, events: [.pageStart(title: pageTitle, page: pageNo, total: pageTotal)])
        }
        if let m = Self.match(Self.searchHeader, t) {
            section = .search
            searchTitle = m[0]
            searchQuery = m[1]
            hits.removeAll()
            return Result(libraryLine: true, events: [])
        }
        if let m = Self.match(Self.searchNone, t) {
            return Result(libraryLine: true, events: [.searchResults(title: m[0], query: m[1], hits: [])])
        }
        if t.hasPrefix("已自动跳转到第") { return Result(libraryLine: true, events: [.notice(t)]) }
        if t == "已是最后一页。" || t == "已是第一页。" { return Result(libraryLine: true, events: [.notice(t)]) }
        if t.hasPrefix("已关闭当前图书") { return Result(libraryLine: true, events: [.closed]) }
        if t.hasPrefix("请先用 /library open") { return Result(libraryLine: true, events: [.needsReopen]) }
        if t.hasPrefix("正在从节点") || t.hasPrefix("正在节点") {
            return Result(libraryLine: true, events: [.loading(t)])
        }
        if Self.errorPrefixes.contains(where: { t.hasPrefix($0) }) {
            return Result(libraryLine: true, events: [.error(t)])
        }
        if t.hasPrefix("用 /library 查看可用序号") { return Result(libraryLine: true, events: []) }
        return .none
    }

    private func pageEvent() -> LibraryEvent {
        .page(LibraryPage(title: pageTitle, page: pageNo, total: pageTotal, paragraphs: Self.reflow(pageLines)))
    }

    private func searchEvent() -> LibraryEvent {
        .searchResults(title: searchTitle, query: searchQuery, hits: hits)
    }

    static func parseBook(_ t: String) -> LibraryBook? {
        guard let m = match(bookLine, t) else { return nil }
        return LibraryBook(
            index: Int(m[0]) ?? 0,
            format: m[1].uppercased(),
            name: m[2],
            size: m[3],
            origin: m[4].isEmpty ? nil : m[4],
            bookmarkPage: m.count > 5 && !m[5].isEmpty ? Int(m[5]) : nil
        )
    }

    static func reflow(_ lines: [String], wrapBytes: Int = defaultWrapBytes) -> [String] {
        let trimmed = lines.map { trimEnd($0) }.filter { !$0.isEmpty }
        guard !trimmed.isEmpty else { return [] }
        let observedMax = trimmed.map { utf8Len($0) }.max() ?? 0
        let budget = observedMax > wrapBytes ? observedMax : wrapBytes
        var out: [String] = []
        var cur = trimmed[0]
        var curLast = trimmed[0]
        for i in 1..<trimmed.count {
            let next = trimmed[i]
            let lastLen = utf8Len(curLast)
            let nextTrim = next.drop(while: { $0 == " " })
            let nextFirst = firstCodePoint(String(nextTrim))
            let firstLen = utf8Len(nextFirst)
            let prevChar = curLast.last ?? " "
            let nextChar = nextTrim.first ?? " "
            if next.hasPrefix(" ") {
                cur += next
            } else if lastLen + firstLen > budget {
                cur += next
            } else if lastLen + 1 + firstLen > budget && isWordChar(prevChar) && isWordChar(nextChar) {
                cur += " " + next
            } else {
                let piece = cur.trimmingCharacters(in: .whitespacesAndNewlines)
                if !piece.isEmpty { out.append(piece) }
                cur = next
            }
            curLast = next
        }
        let last = cur.trimmingCharacters(in: .whitespacesAndNewlines)
        if !last.isEmpty { out.append(last) }
        return out
    }

    // MARK: - helpers

    private static func trimEnd(_ s: String) -> String {
        var end = s.endIndex
        while end > s.startIndex {
            let i = s.index(before: end)
            if s[i] == " " || s[i] == "\t" { end = i } else { break }
        }
        return String(s[..<end])
    }

    private static func utf8Len(_ s: String) -> Int {
        s.utf8.count
    }

    private static func firstCodePoint(_ s: String) -> String {
        guard let first = s.unicodeScalars.first else { return "" }
        return String(first)
    }

    private static func isWordChar(_ c: Character) -> Bool {
        guard let v = c.unicodeScalars.first?.value else { return false }
        return v < 0x2E80 && (c.isLetter || c.isNumber)
    }

    private static func matches(_ re: NSRegularExpression, _ s: String) -> Bool {
        let range = NSRange(s.startIndex..., in: s)
        return re.firstMatch(in: s, options: [], range: range) != nil
    }

    /// Capture groups 1… as strings (empty if missing).
    private static func match(_ re: NSRegularExpression, _ s: String) -> [String]? {
        let range = NSRange(s.startIndex..., in: s)
        guard let m = re.firstMatch(in: s, options: [], range: range) else { return nil }
        var out: [String] = []
        for i in 1..<m.numberOfRanges {
            let r = m.range(at: i)
            if r.location == NSNotFound {
                out.append("")
            } else if let swift = Range(r, in: s) {
                out.append(String(s[swift]))
            } else {
                out.append("")
            }
        }
        return out
    }
}

/// Captures the `[*]` reply of a `/dict` sent from the reader so it can be shown in a sheet
/// instead of the chat log. The server sends no end marker; callers finish after a quiet period.
/// Mirrors `server.py` `_handle_dict` / `dict_lookup.lookup_lines`.
final class DictCapture {
    static let maxLen = 64

    private var word: String?
    private var started = false
    private var lines: [String] = []

    var isActive: Bool { word != nil }

    private static let header = try! NSRegularExpression(pattern: #"^---\s*(英→中|中→英|汉语)：.*---$"#)
    private static let errorPrefixes = ["词典查询失败", "请提供要查询的词语", "query too long", "missing word", "empty query"]
    private static let modeAliases: Set<String> = ["en", "eng", "英", "ce", "cn", "中", "中英", "zh", "hh", "汉", "汉语"]

    func begin(_ word: String) {
        self.word = word
        started = false
        lines = []
    }

    func cancel() {
        word = nil
        started = false
        lines = []
    }

    /// Returns true when the star body belongs to the pending lookup.
    func feed(_ body: String) -> Bool {
        guard let w = word else { return false }
        let t = body.trimmingCharacters(in: .whitespacesAndNewlines)
        let range = NSRange(t.startIndex..., in: t)
        if Self.header.firstMatch(in: t, range: range) != nil {
            started = true
            lines.append(t)
            return true
        }
        if !started {
            guard Self.errorPrefixes.contains(where: { t.hasPrefix($0) }) else { return false }
            started = true
            lines.append(t)
            return true
        }
        if body.isEmpty || body.hasPrefix("  ") || t.hasPrefix("英 [") || t.hasPrefix("美 [")
            || t.hasPrefix("[") || t.hasPrefix(w)
        {
            var end = body.endIndex
            while end > body.startIndex, body[body.index(before: end)].isWhitespace { end = body.index(before: end) }
            lines.append(String(body[..<end]))
            return true
        }
        return false
    }

    /// Ends the lookup; nil when nothing arrived yet.
    func finish() -> [String]? {
        guard started else { return nil }
        let out = lines
        cancel()
        return out
    }

    /// Selected text → query word, or nil when nothing usable is selected.
    static func normalize(_ selected: String) -> String? {
        let edge = CharacterSet.whitespacesAndNewlines
            .union(.punctuationCharacters)
            .union(.symbols)
            .union(CharacterSet(charactersIn: "\u{3000}"))
        let words = selected.trimmingCharacters(in: edge)
            .split(whereSeparator: { $0.isWhitespace })
        let out = words.joined(separator: " ")
        return out.isEmpty ? nil : out
    }

    static func hasCjk(_ s: String) -> Bool {
        s.unicodeScalars.contains { (0x4E00...0x9FFF).contains($0.value) }
    }

    /// Plain `/dict 词` gives 中→英 + 汉语 for Chinese; force a mode when the word itself
    /// would be read as a mode alias or as `help`.
    static func command(for word: String) -> String {
        guard hasCjk(word) else { return "/dict en \(word)" }
        let first = word.split(separator: " ").first.map { String($0).lowercased() } ?? ""
        return modeAliases.contains(first) || word == "帮助" ? "/dict cn \(word)" : "/dict \(word)"
    }
}
