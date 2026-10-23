import Foundation

struct NewsItem: Identifiable, Equatable {
    let id: String
    var categoryLabel: String
    var categoryToken: String
    let index: Int
    let source: String
    let title: String
    var body: String
    var link: String
}

enum NewsEvent: Equatable {
    case items([NewsItem])
    case notice(String)
}

/// `/news` rows wrap on a narrow PTY, so titles are parsed from the joined blob.
final class NewsParser {
    struct Result {
        let newsLine: Bool
        let events: [NewsEvent]
        static let none = Result(newsLine: false, events: [])
    }

    private var blob = ""
    private var samples: [String] = []
    private var lastPeeled = ""

    func reset() {
        blob = ""
        samples = []
        lastPeeled = ""
    }

    func snapshotSamples() -> [String] { samples }

    func flush() -> [NewsEvent] {
        let list = Self.extract(from: blob)
        return list.isEmpty ? [] : [.items(list)]
    }

    func feed(_ raw: String) -> Result {
        let peeled = Self.peel(raw)
        let t = peeled.trimmingCharacters(in: .whitespacesAndNewlines)
        if t.isEmpty { return .none }
        if samples.count < 12, !samples.contains(t) {
            samples.append(String(t.prefix(160)))
        }
        if peeled != lastPeeled {
            blob.append(peeled)
            blob.append("\n")
            lastPeeled = peeled
        }
        let list = Self.extract(from: blob)
        let notice = Self.isNewsNotice(t)
        if list.isEmpty && !notice { return Result(newsLine: true, events: []) }
        var ev: [NewsEvent] = list.isEmpty ? [] : [.items(list)]
        if notice { ev.append(.notice(t)) }
        return Result(newsLine: true, events: ev)
    }

    /// Drop clocks / `[#room]` / `[*]`.
    static func peel(_ raw: String) -> String {
        var s = ChatLineParsers.normalizeForParse(raw)
        if let body = ChatLineParsers.parseGameStarBody(s) { return body }
        if let star = s.range(of: "[*]") {
            s = String(s[star.upperBound...])
            if s.first == " " { s.removeFirst() }
            return s
        }
        return s
    }

    static func inferCategoryToken(_ label: String) -> String {
        let l = label.lowercased()
        if l.contains("中文") || l.contains("cn") || l.contains("chinese") { return "中文" }
        if l.contains("国际") || l.contains("world") || l.contains("intl") { return "国际" }
        if l.contains("科技") || l.contains("tech") { return "科技" }
        return "all"
    }

    private static let titleRe = try! NSRegularExpression(
        pattern: #"(\d+)\s*[\.．。、]\s*[\[【]?([^\]】]{1,80}?)[\]】]\s*(.+?)(?=\s+\d+\s*[\.．。、]\s*[\[【]?[^\]】]{1,80}?[\]】]|$)"#,
        options: [.dotMatchesLineSeparators]
    )
    private static let sectionRe = try! NSRegularExpression(
        pattern: #"---\s*([^-\n]{1,40}?)\s*---"#
    )
    private static let urlRe = try! NSRegularExpression(
        pattern: #"https?://[^\s]+"#,
        options: [.caseInsensitive]
    )

    static func extract(from blob: String) -> [NewsItem] {
        let hay = blob
        var items: [NewsItem] = []
        var order: [String] = []
        var seen = Set<String>()
        let full = NSRange(hay.startIndex..., in: hay)

        var catRanges: [(range: NSRange, label: String, token: String)] = []
        sectionRe.enumerateMatches(in: hay, range: full) { m, _, _ in
            guard let m, let r = Range(m.range(at: 1), in: hay) else { return }
            let label = String(hay[r]).trimmingCharacters(in: .whitespacesAndNewlines)
            guard looksLikeNewsSection(label) else { return }
            catRanges.append((m.range, label, inferCategoryToken(label)))
        }

        func category(at loc: Int) -> (String, String) {
            var best: (String, String) = ("新闻", "all")
            for c in catRanges where c.range.location <= loc {
                best = (c.label, c.token)
            }
            return best
        }

        titleRe.enumerateMatches(in: hay, range: full) { m, _, _ in
            guard let m,
                  let iR = Range(m.range(at: 1), in: hay),
                  let sR = Range(m.range(at: 2), in: hay),
                  let tR = Range(m.range(at: 3), in: hay)
            else { return }
            let idx = Int(hay[iR]) ?? 0
            let source = String(hay[sR]).trimmingCharacters(in: .whitespacesAndNewlines)
            var rest = String(hay[tR]).replacingOccurrences(of: "\n", with: " ")
            rest = rest.replacingOccurrences(of: #"\s+"#, with: " ", options: .regularExpression)
                .trimmingCharacters(in: .whitespacesAndNewlines)
            if let cut = rest.range(of: "可用：") ?? rest.range(of: "Usage:") {
                rest = String(rest[..<cut.lowerBound]).trimmingCharacters(in: .whitespaces)
            }
            var link = ""
            if let um = urlRe.firstMatch(in: rest, range: NSRange(rest.startIndex..., in: rest)),
               let ur = Range(um.range, in: rest) {
                link = String(rest[ur])
                while link.last == "." || link.last == ")" || link.last == "]" { link.removeLast() }
                rest = String(rest[..<ur.lowerBound]).trimmingCharacters(in: .whitespaces)
            }
            // Drop RSS body after the title: keep up to ~120 chars or first sentence-ish.
            let title = shortenTitle(rest)
            guard idx > 0, !source.isEmpty, !title.isEmpty else { return }
            let (label, token) = category(at: m.range.location)
            let id = "\(token):\(idx)"
            if seen.contains(id) { return }
            seen.insert(id)
            order.append(id)
            items.append(NewsItem(
                id: id,
                categoryLabel: label,
                categoryToken: token,
                index: idx,
                source: source,
                title: title,
                body: "",
                link: link
            ))
        }
        return order.compactMap { id in items.first(where: { $0.id == id }) }
    }

    private static func shortenTitle(_ raw: String) -> String {
        let t = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard t.count > 160 else { return t }
        if let dot = t.range(of: ". "),
           t.distance(from: t.startIndex, to: dot.upperBound) < 160
        {
            return String(t[..<dot.upperBound]).trimmingCharacters(in: .whitespaces)
        }
        return String(t.prefix(160)).trimmingCharacters(in: .whitespaces) + "…"
    }

    private static func looksLikeNewsSection(_ label: String) -> Bool {
        let l = label.lowercased()
        if l.contains("图书馆") || l.contains("library") { return false }
        return l.contains("新闻") || l.contains("news") ||
            l.contains("中文") || l.contains("国际") || l.contains("科技") ||
            l.contains("world") || l.contains("tech")
    }

    static func isNewsNotice(_ t: String) -> Bool {
        let prefixes = [
            "可用：", "单条更长提要", "抓取网页正文", "暂时没有取到新闻",
            "Usage:", "Aliases:", "说明：", "例：", "序号须为",
        ]
        return prefixes.contains { t.hasPrefix($0) }
    }

    static func parseOriginalUrl(_ t: String) -> String? {
        let range = NSRange(t.startIndex..., in: t)
        guard let m = urlRe.firstMatch(in: t, range: range),
              let r = Range(m.range, in: t)
        else { return nil }
        var url = String(t[r])
        while url.last == "." || url.last == ")" || url.last == "]" { url.removeLast() }
        return url
    }
}
