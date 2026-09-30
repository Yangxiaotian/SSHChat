import Foundation

enum GameKind: String, CaseIterable, Identifiable {
    case xiangqi, chess, go, gomoku, doushou, junqi, darkchess, reversi
    case battleship, holdem, zjh, niutou, mahjong, sanguo, werewolf

    var id: String { rawValue }

    var title: String {
        switch self {
        case .xiangqi: return "中国象棋"
        case .chess: return "国际象棋"
        case .go: return "围棋"
        case .gomoku: return "五子棋"
        case .doushou: return "斗兽棋"
        case .junqi: return "军棋"
        case .darkchess: return "暗棋"
        case .reversi: return "黑白棋"
        case .battleship: return "海战棋"
        case .holdem: return "德州扑克"
        case .zjh: return "炸金花"
        case .niutou: return "谁是牛头王"
        case .mahjong: return "麻将"
        case .sanguo: return "三国杀"
        case .werewolf: return "狼人杀"
        }
    }
}

struct Board: Equatable {
    var rows: Int
    var cols: Int
    var cells: [String]
    var last: Set<Int> = []
    var rowLabels: [Int] = []
    var colLabels: [String] = []

    func at(_ r: Int, _ c: Int) -> String { cells[r * cols + c] }
    func isLast(_ r: Int, _ c: Int) -> Bool { last.contains(r * cols + c) }
}

struct Seat: Equatable, Identifiable {
    var id: Int { index }
    var index: Int
    var name: String
    var detail: String
    var active: Bool = false
    var alive: Bool = true
    var hp: Int? = nil
    var maxHp: Int? = nil
}

struct GameSnapshot {
    var kind: GameKind
    var state: String
    var header: String
    var board: Board? = nil
    var board2: Board? = nil
    var flipped: Bool = false
    var players: [String: String] = [:]
    var turnSide: String? = nil
    var turnName: String? = nil
    var info: [String] = []
    var seats: [Seat] = []
    var hand: [String] = []
    var community: [String] = []
    var rows: [([Int], Int)] = []
    var fields: [String: String] = [:]
    var hints: [String] = []
    var messages: [String] = []

    var isPlaying: Bool {
        let s = state.lowercased()
        return s == "playing" || s == "night" || s == "day"
            || s.contains("进行") || s.contains("in progress")
            || s.contains("选行") || s.contains("row pick")
    }
    var isWaiting: Bool {
        let s = state.lowercased()
        return s == "waiting" || s.contains("等待开始") || s.contains("waiting to start")
    }
    var isSetup: Bool { state.lowercased() == "setup" }
    var isEnded: Bool {
        let s = state.lowercased()
        return s == "ended" || s.contains("结束")
    }
}

enum GameParser {
    private struct Header {
        let kind: GameKind
        let regex: NSRegularExpression
        init(_ kind: GameKind, _ pattern: String) {
            self.kind = kind
            self.regex = try! NSRegularExpression(pattern: pattern)
        }
    }

    private static let headers: [Header] = [
        Header(.xiangqi, #"^xiangqi (?:对局)?[（(]([^）)]*)[）)]"#),
        Header(.chess, #"^chess (?:对局)?[（(]([^）)]*)[）)]"#),
        Header(.go, #"^go (?:对局)?[（(]([^）)]*)[）)]"#),
        Header(.gomoku, #"^gomoku (?:对局)?[（(]([^）)]*)[）)]"#),
        Header(.doushou, #"^doushou (?:对局)?[（(]([^）)]*)[）)]"#),
        Header(.darkchess, #"^darkchess (?:对局)?[（(]([^）)]*)[）)]"#),
        Header(.reversi, #"^reversi game [（(]([^）)]*)[）)]"#),
        Header(.battleship, #"^battleship game [（(]([^）)]*)[）)]"#),
        Header(.junqi, #"^junqi game [（(]([^）)]*)[）)]"#),
        Header(.holdem, #"^(?:德州扑克 状态|Hold'em status)[：:]\s*(.+)$"#),
        Header(.zjh, #"^(?:炸金花 状态|Zha Jin Hua status)[：:]\s*(.+)$"#),
        Header(.niutou, #"^(?:牛头王 状态|6 Nimmt! status)[：:]\s*(.+)$"#),
        Header(.mahjong, #"^(?:麻将 状态|Mahjong status)[：:]\s*(.+)$"#),
        Header(.sanguo, #"^三国杀·军争\s+(\S+)"#),
        Header(.werewolf, #"^werewolf state:\s*(\S+)"#),
        Header(.werewolf, #"^Werewolf started\. (Night) \d+"#),
        Header(.werewolf, #"^\S+ joined werewolf \(\d+ players\)"#),
    ]

    static func detectHeader(_ line: String) -> (GameKind, String)? {
        let t = line.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty else { return nil }
        let range = NSRange(t.startIndex..., in: t)
        for h in headers {
            guard let m = h.regex.firstMatch(in: t, range: range),
                  m.numberOfRanges > 1,
                  let r = Range(m.range(at: 1), in: t) else { continue }
            var state = String(t[r]).trimmingCharacters(in: .whitespaces)
            if h.kind == .werewolf && state == "Night" { state = "night" }
            if h.kind == .werewolf && state.isEmpty { state = "waiting" }
            return (h.kind, state)
        }
        return nil
    }

    static func parseLatest(lines: [String], myName: String) -> GameSnapshot? {
        var idx = -1
        var head: (GameKind, String)?
        for i in stride(from: lines.count - 1, through: 0, by: -1) {
            if let h = detectHeader(lines[i]) {
                idx = i
                head = h
                break
            }
        }
        guard let head, idx >= 0 else { return nil }
        let block = Array(lines[idx...])
        return parseBlock(kind: head.0, state: head.1, block: block, myName: myName)
    }

    static func parseBlock(kind: GameKind, state: String, block: [String], myName: String) -> GameSnapshot? {
        switch kind {
        case .xiangqi: return parseXiangqi(state, block)
        case .chess: return parseChess(state, block)
        case .go, .gomoku: return parseStones(kind, state, block)
        case .reversi: return parseReversi(state, block)
        case .darkchess: return parseDarkchess(state, block)
        case .doushou: return parseDoushou(state, block)
        case .junqi: return parseJunqi(state, block)
        case .battleship: return parseBattleship(state, block)
        case .holdem, .zjh: return parsePoker(kind, state, block)
        case .niutou: return parseNiutou(state, block, myName)
        case .mahjong: return parseMahjong(state, block)
        case .sanguo: return parseSanguo(state, block)
        case .werewolf: return parseWerewolf(state, block)
        }
    }

    // MARK: - helpers

    private static let rowLine = try! NSRegularExpression(pattern: #"^\s*(\d{1,2})\s(.*)$"#)
    private static let sideWords: [String: String] = [
        "红": "red", "红方": "red", "Red": "red",
        "黑": "black", "黑方": "black", "Black": "black",
        "白": "white", "白方": "white", "White": "white",
        "蓝": "blue", "Blue": "blue",
    ]

    private static func match(_ re: NSRegularExpression, _ s: String) -> [String]? {
        let range = NSRange(s.startIndex..., in: s)
        guard let m = re.firstMatch(in: s, range: range) else { return nil }
        return (0..<m.numberOfRanges).compactMap { i -> String? in
            guard let r = Range(m.range(at: i), in: s) else { return nil }
            return String(s[r])
        }
    }

    private static func findAll(_ re: NSRegularExpression, _ s: String) -> [[String]] {
        let range = NSRange(s.startIndex..., in: s)
        return re.matches(in: s, range: range).compactMap { m -> [String]? in
            (0..<m.numberOfRanges).compactMap { i -> String? in
                guard let r = Range(m.range(at: i), in: s) else { return nil }
                return String(s[r])
            }
        }
    }

    private static func headerPlayers(_ header: String) -> [String: String] {
        var out: [String: String] = [:]
        let tag = try! NSRegularExpression(pattern: #"(红|黑|白|蓝|Red|Black|White|Blue)[：:]\s*([^\s（(]+)"#)
        for m in findAll(tag, header) where m.count >= 3 {
            if let side = sideWords[m[1]], m[2] != "empty", m[2] != "空" {
                if out[side] == nil { out[side] = m[2] }
            }
        }
        let num = try! NSRegularExpression(pattern: #"(?:玩家|Player )([12])[：:]\s*([^\s（(]+)"#)
        for m in findAll(num, header) where m.count >= 3 {
            if out["p\(m[1])"] == nil { out["p\(m[1])"] = m[2] }
        }
        return out
    }

    private static let turnPatterns: [NSRegularExpression] = [
        try! NSRegularExpression(pattern: #"轮到\s*(红方|黑方|白方)\s*([^\s（(]+)"#),
        try! NSRegularExpression(pattern: #"^(Red|Black|White)\s+(\S+)\s+(?:to move|行棋)"#),
        try! NSRegularExpression(pattern: #"^Turn:\s*(Black|White)\s+(\S+)"#),
        try! NSRegularExpression(pattern: #"^Turn:\s*()([^\s(]+)"#),
        try! NSRegularExpression(pattern: #"轮到\s*#\d+\s*()(\S+)\s*的回合"#),
        try! NSRegularExpression(pattern: #"当前回合[：:]\s*#\d+\s*()(\S+)"#),
        try! NSRegularExpression(pattern: #"轮到[：:]\s*()([^\s（(，,]+)"#),
        try! NSRegularExpression(pattern: #"当前轮到[：:]\s*()(\S+)"#),
    ]

    private static func findTurn(_ lines: [String]) -> (String?, String?) {
        for i in stride(from: lines.count - 1, through: 0, by: -1) {
            let t = lines[i].trimmingCharacters(in: .whitespaces)
            for p in turnPatterns {
                if let m = match(p, t), m.count >= 3 {
                    return (sideWords[m[1]], m[2].isEmpty ? nil : m[2])
                }
            }
        }
        return (nil, nil)
    }

    private static func lastLineMatching(_ lines: [String], _ pattern: String) -> String? {
        let re = try! NSRegularExpression(pattern: pattern)
        for i in stride(from: lines.count - 1, through: 0, by: -1) {
            let t = lines[i].trimmingCharacters(in: .whitespaces)
            if match(re, t) != nil { return t }
        }
        return nil
    }

    private static func messagesAfter(_ block: [String], lastUsed: Int) -> [String] {
        Array(block.dropFirst(lastUsed + 1)
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
            .suffix(4))
    }

    private static func lastTurnIndex(_ block: [String], fallback: Int) -> Int {
        var best = fallback
        for i in (fallback + 1)..<block.count {
            let t = block[i].trimmingCharacters(in: .whitespaces)
            let cont = turnPatterns.contains { match($0, t) != nil }
                || t.hasPrefix("上一步") || t.hasPrefix("Last move")
                || t.hasPrefix("图例") || t.hasPrefix("Legend")
                || t.hasPrefix("  ") || t.isEmpty || block[i].hasPrefix(" ")
            if cont { best = i } else { break }
        }
        return best
    }

    private struct Rows {
        var rows: [[String]]
        var labels: [Int]
        var lastIdx: Int
    }

    private static func collectRows(
        _ block: [String], cols: Int, maxRows: Int, token: NSRegularExpression,
        numbered: Bool = true, tokenCount: Int? = nil
    ) -> Rows {
        let need = tokenCount ?? cols
        var rows: [[String]] = []
        var labels: [Int] = []
        var lastIdx = 0
        for (i, raw) in block.enumerated() {
            if i == 0 || rows.count >= maxRows { continue }
            var label = 0
            let rest: String
            if numbered {
                guard let m = match(rowLine, raw), m.count >= 3 else { continue }
                label = Int(m[1]) ?? 0
                rest = m[2]
            } else {
                rest = raw
            }
            let toks = findAll(token, rest).compactMap { $0.first }
            guard toks.count == need else { continue }
            rows.append(toks)
            labels.append(label)
            lastIdx = i
        }
        return Rows(rows: rows, labels: labels, lastIdx: lastIdx)
    }

    // MARK: - xiangqi

    private static let xqToken = try! NSRegularExpression(pattern: #"[+\-!][^\s·*+\-!]|·|\*"#)
    private static let xqRedOnly = CharacterSet(charactersIn: "帅仕相兵")
    private static let xqBlackOnly = CharacterSet(charactersIn: "将士象卒")

    private static func xqChar(_ letter: Character, red: Bool) -> Character {
        switch letter {
        case "R": return "车"
        case "H", "N": return "马"
        case "E", "B": return red ? "相" : "象"
        case "A": return red ? "仕" : "士"
        case "G", "K": return red ? "帅" : "将"
        case "C": return "炮"
        case "S", "P": return red ? "兵" : "卒"
        default: return letter
        }
    }

    private static func parseXiangqi(_ state: String, _ block: [String]) -> GameSnapshot? {
        let rows = collectRows(block, cols: 9, maxRows: 10, token: xqToken, numbered: false)
        guard rows.rows.count == 10 else { return nil }
        let flipped = block.contains { $0.contains("己方在下方") || $0.contains("you are at the bottom") }
            || (block.dropFirst().first(where: { !$0.trimmingCharacters(in: .whitespaces).isEmpty })?
                .trimmingCharacters(in: .whitespaces).hasPrefix("一") == true)
        let (turnSide, turnName) = findTurn(block)
        let moverRed: Bool = {
            switch turnSide {
            case "red": return false
            case "black": return true
            default: return true
            }
        }()
        var cells: [String] = []
        var last = Set<Int>()
        for (r, toks) in rows.rows.enumerated() {
            for (c, tok) in toks.enumerated() {
                let i = r * 9 + c
                if tok == "·" { cells.append(""); continue }
                if tok == "*" { cells.append(""); last.insert(i); continue }
                let chars = Array(tok)
                guard chars.count >= 2 else { cells.append(""); continue }
                let mark = chars[0]
                let sym = chars[1]
                let red: Bool = {
                    switch mark {
                    case "+": return true
                    case "-": return false
                    default:
                        if String(sym).rangeOfCharacter(from: xqRedOnly) != nil { return true }
                        if String(sym).rangeOfCharacter(from: xqBlackOnly) != nil { return false }
                        return moverRed
                    }
                }()
                if mark == "!" { last.insert(i) }
                let ch: Character = (sym >= "A" && sym <= "Z") ? xqChar(sym, red: red) : sym
                cells.append((red ? "r" : "b") + String(ch))
            }
        }
        let info = [lastLineMatching(block, #"^(上一步|Last move)[：:].*"#)].compactMap { $0 }
        return GameSnapshot(
            kind: .xiangqi, state: state, header: block[0].trimmingCharacters(in: .whitespaces),
            board: Board(rows: 10, cols: 9, cells: cells, last: last), flipped: flipped,
            players: headerPlayers(block[0]), turnSide: turnSide, turnName: turnName, info: info,
            messages: messagesAfter(block, lastUsed: lastTurnIndex(block, fallback: rows.lastIdx))
        )
    }

    // MARK: - chess

    private static let chessToken = try! NSRegularExpression(pattern: #"\((.)\)|([♔♕♖♗♘♙♚♛♜♝♞♟·])"#)
    private static let chessFiles = try! NSRegularExpression(pattern: #"^\s*([a-h])(?:\s+[a-h]){7}\s*$"#)

    private static func parseChess(_ state: String, _ block: [String]) -> GameSnapshot? {
        var rows: [[(String, Bool)]] = []
        var ranks: [Int] = []
        var files: [String] = []
        var lastIdx = 0
        for (i, raw) in block.enumerated() {
            if i == 0 { continue }
            if files.isEmpty, match(chessFiles, raw) != nil {
                files = raw.trimmingCharacters(in: .whitespaces).split(whereSeparator: { $0.isWhitespace }).map(String.init)
                continue
            }
            if rows.count >= 8 { continue }
            guard let m = match(rowLine, raw), m.count >= 3 else { continue }
            let toks = findAll(chessToken, m[2]).compactMap { g -> (String, Bool)? in
                if g.count >= 2, !g[1].isEmpty { return (g[1], true) }
                if g.count >= 3 { return (g[2], false) }
                return nil
            }
            guard toks.count == 8 else { continue }
            rows.append(toks)
            ranks.append(Int(m[1]) ?? 0)
            lastIdx = i
        }
        guard rows.count == 8, files.count == 8 else { return nil }
        var cells: [String] = []
        var last = Set<Int>()
        for (r, toks) in rows.enumerated() {
            for (c, pair) in toks.enumerated() {
                cells.append(pair.0 == "·" ? "" : pair.0)
                if pair.1 { last.insert(r * 8 + c) }
            }
        }
        let (turnSide, turnName) = findTurn(block)
        let info = [lastLineMatching(block, #"^(上一步|Last move)[：:].*"#)].compactMap { $0 }
        return GameSnapshot(
            kind: .chess, state: state, header: block[0].trimmingCharacters(in: .whitespaces),
            board: Board(rows: 8, cols: 8, cells: cells, last: last, rowLabels: ranks, colLabels: files),
            flipped: files.first == "h",
            players: headerPlayers(block[0]), turnSide: turnSide, turnName: turnName, info: info,
            messages: messagesAfter(block, lastUsed: lastTurnIndex(block, fallback: lastIdx))
        )
    }

    // MARK: - stones

    private static let stoneToken = try! NSRegularExpression(pattern: #"\(([.#o])\)|!?([.#o])"#)

    private static func stoneRows(_ block: [String], maxRows: Int) -> (cells: [String], last: Set<Int>, size: Int, lastIdx: Int)? {
        var grid: [[(String, Bool)]] = []
        var lastIdx = 0
        for (i, raw) in block.enumerated() {
            if i == 0 || grid.count >= maxRows { continue }
            guard let m = match(rowLine, raw), m.count >= 3, Int(m[1]) == grid.count + 1 else { continue }
            let toks = findAll(stoneToken, m[2]).compactMap { g -> (String, Bool)? in
                if g.count >= 2, !g[1].isEmpty { return (g[1], true) }
                if g.count >= 3 {
                    let full = g[0]
                    return (g[2], full.hasPrefix("!"))
                }
                return nil
            }
            if toks.count < 5 { continue }
            if !grid.isEmpty, toks.count != grid[0].count { continue }
            grid.append(toks)
            lastIdx = i
        }
        guard !grid.isEmpty, grid.count == grid[0].count else { return nil }
        let size = grid.count
        var cells: [String] = []
        var last = Set<Int>()
        for (r, toks) in grid.enumerated() {
            for (c, pair) in toks.enumerated() {
                switch pair.0 {
                case "#": cells.append("B")
                case "o": cells.append("W")
                default: cells.append("")
                }
                if pair.1 { last.insert(r * size + c) }
            }
        }
        return (cells, last, size, lastIdx)
    }

    private static func parseStones(_ kind: GameKind, _ state: String, _ block: [String]) -> GameSnapshot? {
        guard let r = stoneRows(block, maxRows: 19) else { return nil }
        let (turnSide, turnName) = findTurn(block)
        var info: [String] = []
        if let k = lastLineMatching(block, #"^(贴目|Komi)[：:].*"#) { info.append(k) }
        if let k = lastLineMatching(block, #"^(上一步|Last move)[：:].*"#) { info.append(k) }
        return GameSnapshot(
            kind: kind, state: state, header: block[0].trimmingCharacters(in: .whitespaces),
            board: Board(rows: r.size, cols: r.size, cells: r.cells, last: r.last),
            players: headerPlayers(block[0]), turnSide: turnSide, turnName: turnName, info: info,
            messages: messagesAfter(block, lastUsed: lastTurnIndex(block, fallback: r.lastIdx))
        )
    }

    private static func parseReversi(_ state: String, _ block: [String]) -> GameSnapshot? {
        guard let r = stoneRows(block, maxRows: 8) else { return nil }
        let (turnSide, turnName) = findTurn(block)
        let info = [lastLineMatching(block, #"^(Score|比分)[：:].*"#)].compactMap { $0 }
        return GameSnapshot(
            kind: .reversi, state: state, header: block[0].trimmingCharacters(in: .whitespaces),
            board: Board(rows: r.size, cols: r.size, cells: r.cells, last: r.last),
            players: headerPlayers(block[0]), turnSide: turnSide, turnName: turnName, info: info,
            messages: messagesAfter(block, lastUsed: lastTurnIndex(block, fallback: r.lastIdx))
        )
    }

    // MARK: - darkchess

    private static let darkToken = try! NSRegularExpression(pattern: #"!?(?:[+\-][^\s!+\-?.]|\?|\.)"#)
    private static let darkLetters: [Character: Character] = [
        "G": "将", "A": "士", "E": "象", "R": "车", "H": "马", "C": "炮", "S": "卒",
    ]

    private static func parseDarkchess(_ state: String, _ block: [String]) -> GameSnapshot? {
        let rows = collectRows(block, cols: 8, maxRows: 4, token: darkToken)
        guard rows.rows.count == 4 else { return nil }
        var cells: [String] = []
        var last = Set<Int>()
        for (r, toks) in rows.rows.enumerated() {
            for (c, raw) in toks.enumerated() {
                var tok = raw
                if tok.hasPrefix("!") {
                    last.insert(r * 8 + c)
                    tok = String(tok.dropFirst())
                }
                if tok == "?" { cells.append("?") }
                else if tok == "." { cells.append("") }
                else {
                    let chars = Array(tok)
                    let ch = darkLetters[chars[1]] ?? chars[1]
                    cells.append((chars[0] == "+" ? "r" : "b") + String(ch))
                }
            }
        }
        var players = headerPlayers(block[0])
        if let m = lastLineMatching(block, #"(?:阵营|Sides)[：:]\s*P1\s*(\S+)\s+P2\s*(\S+)"#),
           let g = match(try! NSRegularExpression(pattern: #"(?:阵营|Sides)[：:]\s*P1\s*(\S+)\s+P2\s*(\S+)"#), m),
           g.count >= 3 {
            players["p1side"] = g[1].hasPrefix("红") || g[1].hasPrefix("red") ? "red"
                : (g[1].hasPrefix("黑") || g[1].hasPrefix("black") ? "black" : "")
            players["p2side"] = g[2].hasPrefix("红") || g[2].hasPrefix("red") ? "red"
                : (g[2].hasPrefix("黑") || g[2].hasPrefix("black") ? "black" : "")
        }
        let (turnSide, turnName) = findTurn(block)
        let info = [lastLineMatching(block, #"^(上一步|Last move)[：:].*"#)].compactMap { $0 }
        return GameSnapshot(
            kind: .darkchess, state: state, header: block[0].trimmingCharacters(in: .whitespaces),
            board: Board(rows: 4, cols: 8, cells: cells, last: last),
            players: players, turnSide: turnSide, turnName: turnName, info: info,
            messages: messagesAfter(block, lastUsed: lastTurnIndex(block, fallback: rows.lastIdx))
        )
    }

    // MARK: - doushou

    private static let dsToken = try! NSRegularExpression(pattern: #"!?[+\-][^\s·!]|!|黑陷|黑穴|红陷|红穴|河|·|bT|bD|rT|rD|RV"#)
    private static let dsLetters: [Character: Character] = [
        "R": "鼠", "C": "猫", "D": "狗", "W": "狼", "P": "豹", "T": "虎", "L": "狮", "E": "象",
    ]

    private static func parseDoushou(_ state: String, _ block: [String]) -> GameSnapshot? {
        let rows = collectRows(block, cols: 7, maxRows: 9, token: dsToken)
        guard rows.rows.count == 9 else { return nil }
        let colRe = try! NSRegularExpression(pattern: #"^\s*([1-7])(?:\s+[1-7]){6}\s*$"#)
        let colLabels: [String] = {
            if let line = block.first(where: { match(colRe, $0) != nil }) {
                return line.trimmingCharacters(in: .whitespaces).split(whereSeparator: { $0.isWhitespace }).map(String.init)
            }
            return (1...7).map(String.init)
        }()
        var cells: [String] = []
        var last = Set<Int>()
        for (r, toks) in rows.rows.enumerated() {
            for (c, raw) in toks.enumerated() {
                var tok = raw
                if tok.hasPrefix("!") {
                    last.insert(r * 7 + c)
                    tok = String(tok.dropFirst())
                }
                let chars = Array(tok)
                if chars.count == 2, chars[0] == "+" || chars[0] == "-" {
                    let ch = dsLetters[chars[1]] ?? chars[1]
                    cells.append((chars[0] == "+" ? "r" : "b") + String(ch))
                } else if tok == "·" || tok == "." || tok == "*" {
                    cells.append("")
                } else {
                    switch tok {
                    case "RV": cells.append("河")
                    case "rD": cells.append("红穴")
                    case "bD": cells.append("黑穴")
                    case "rT": cells.append("红陷")
                    case "bT": cells.append("黑陷")
                    case "河", "红穴", "黑穴", "红陷", "黑陷": cells.append(tok)
                    default: cells.append("")
                    }
                }
            }
        }
        let (turnSide, turnName) = findTurn(block)
        return GameSnapshot(
            kind: .doushou, state: state, header: block[0].trimmingCharacters(in: .whitespaces),
            board: Board(rows: 9, cols: 7, cells: cells, last: last, rowLabels: rows.labels, colLabels: colLabels),
            flipped: rows.labels.first == 9,
            players: headerPlayers(block[0]), turnSide: turnSide, turnName: turnName,
            messages: messagesAfter(block, lastUsed: lastTurnIndex(block, fallback: rows.lastIdx))
        )
    }

    // MARK: - junqi / battleship

    private static let junqiToken = try! NSRegularExpression(pattern: #"!?(?:[+\-][A-Z]|\?|\.)"#)

    private static func parseJunqi(_ state: String, _ block: [String]) -> GameSnapshot? {
        let rows = collectRows(block, cols: 5, maxRows: 12, token: junqiToken)
        guard rows.rows.count == 12 else { return nil }
        var cells: [String] = []
        var last = Set<Int>()
        for (r, toks) in rows.rows.enumerated() {
            for (c, raw) in toks.enumerated() {
                var tok = raw
                if tok.hasPrefix("!") {
                    last.insert(r * 5 + c)
                    tok = String(tok.dropFirst())
                }
                if tok == "?" { cells.append("?") }
                else if tok == "." { cells.append("") }
                else {
                    let chars = Array(tok)
                    cells.append((chars[0] == "+" ? "r" : "b") + String(chars[1]))
                }
            }
        }
        let (turnSide, turnName) = findTurn(block)
        return GameSnapshot(
            kind: .junqi, state: state, header: block[0].trimmingCharacters(in: .whitespaces),
            board: Board(rows: 12, cols: 5, cells: cells, last: last),
            players: headerPlayers(block[0]), turnSide: turnSide, turnName: turnName,
            messages: messagesAfter(block, lastUsed: lastTurnIndex(block, fallback: rows.lastIdx))
        )
    }

    private static let shipToken = try! NSRegularExpression(pattern: #"!?[SXo.?]"#)

    private static func parseBattleship(_ state: String, _ block: [String]) -> GameSnapshot? {
        let rows = collectRows(block, cols: 10, maxRows: 10, token: shipToken, tokenCount: 20)
        guard rows.rows.count == 10 else { return nil }
        var own: [String] = []
        var enemy: [String] = []
        var last = Set<Int>()
        for (r, toks) in rows.rows.enumerated() {
            for (c, raw) in toks.enumerated() {
                let tok = raw.hasPrefix("!") ? String(raw.dropFirst()) : raw
                if c < 10 {
                    if raw.hasPrefix("!") { last.insert(r * 10 + c) }
                    own.append(tok)
                } else {
                    enemy.append(tok)
                }
            }
        }
        let (turnSide, turnName) = findTurn(block)
        return GameSnapshot(
            kind: .battleship, state: state, header: block[0].trimmingCharacters(in: .whitespaces),
            board: Board(rows: 10, cols: 10, cells: own, last: last),
            board2: Board(rows: 10, cols: 10, cells: enemy),
            players: headerPlayers(block[0]), turnSide: turnSide, turnName: turnName,
            messages: messagesAfter(block, lastUsed: lastTurnIndex(block, fallback: rows.lastIdx))
        )
    }

    // MARK: - cards

    static func parseCards(_ text: String) -> [String] {
        let re = try! NSRegularExpression(pattern: #"(黑桃|红桃|梅花|方块|♠|♥|♣|♦)\s*(10|[2-9JQKA])"#)
        return findAll(re, text).compactMap { g -> String? in
            guard g.count >= 3 else { return nil }
            let suit: String = {
                switch g[1] {
                case "黑桃": return "♠"
                case "红桃": return "♥"
                case "梅花": return "♣"
                case "方块": return "♦"
                default: return g[1]
                }
            }()
            return suit + g[2]
        }
    }

    private static func parsePoker(_ kind: GameKind, _ state: String, _ block: [String]) -> GameSnapshot {
        let seatRe = try! NSRegularExpression(pattern: #"^#(\d+)\s+(\S+?)[：:]\s*(?:积分|chips)=(-?\d+)\s*(.*)$"#)
        let kvRe = try! NSRegularExpression(pattern: #"^(底池|Pot|当前注|Current bet|阶段|Street|房主|Host)\s*[=：:]\s*(.+)$"#)
        let handRe = try! NSRegularExpression(pattern: #"^(?:你的手牌|Your hand)[：:]\s*(.*)$"#)
        let comRe = try! NSRegularExpression(pattern: #"^(?:公共牌|Community cards)[：:]\s*(.*)$"#)
        var seats: [Int: Seat] = [:]
        var fields: [String: String] = [:]
        var hand: [String] = []
        var community: [String] = []
        for raw in block.dropFirst() {
            let t = raw.trimmingCharacters(in: .whitespaces)
            if let m = match(seatRe, t), m.count >= 5 {
                let detail = m[4]
                let idx = Int(m[1]) ?? 0
                seats[idx] = Seat(
                    index: idx, name: m[2], detail: "\(m[3]) · \(detail)".trimmingCharacters(in: .whitespaces),
                    active: detail.contains("行动中") || detail.lowercased().contains("acting"),
                    alive: !(detail.contains("弃牌") || detail.contains("出局") || detail.lowercased().contains("fold"))
                )
                continue
            }
            if let m = match(kvRe, t), m.count >= 3 {
                let key: String = {
                    switch m[1] {
                    case "底池", "Pot": return "pot"
                    case "当前注", "Current bet": return "bet"
                    case "阶段", "Street": return "stage"
                    default: return "host"
                    }
                }()
                fields[key] = m[2].trimmingCharacters(in: .whitespaces)
            }
            if let m = match(handRe, t), m.count >= 2 { hand = parseCards(m[1]) }
            if let m = match(comRe, t), m.count >= 2 { community = parseCards(m[1]) }
            if t.contains("闷牌中") || t.contains("still concealed") {
                fields["concealed"] = "1"
                hand = []
            }
        }
        let (_, turnName) = findTurn(block)
        let active = turnName ?? seats.values.first(where: { $0.active })?.name
        return GameSnapshot(
            kind: kind, state: state, header: block[0].trimmingCharacters(in: .whitespaces),
            turnName: active,
            seats: seats.values.sorted { $0.index < $1.index }.map {
                var s = $0; s.active = s.name == active; return s
            },
            hand: hand, community: community, fields: fields,
            messages: Array(block.dropFirst().map { $0.trimmingCharacters(in: .whitespaces) }.filter { t in
                !t.isEmpty && match(seatRe, t) == nil && match(kvRe, t) == nil
                    && match(handRe, t) == nil && match(comRe, t) == nil
                    && !t.hasPrefix("行牌") && !t.hasPrefix("Actions")
                    && !t.hasPrefix("完整对照") && !t.hasPrefix("Full reference")
                    && !t.contains("闷牌中") && !t.contains("still concealed")
            }.suffix(4))
        )
    }

    private static func parseNiutou(_ state: String, _ block: [String], _ myName: String) -> GameSnapshot {
        let seatRe = try! NSRegularExpression(pattern: #"^-\s+(\S+?)[：:]\s*(?:牛头|bulls)=(\d+)[，,]\s*(?:手牌数|cards)=(\d+)"#)
        let rowRe = try! NSRegularExpression(pattern: #"^(?:第(\d)行|Row (\d))[：:]\s*([\d\s]+?)\s*[（(](?:牛头|bulls)=(\d+)[）)]"#)
        let roundRe = try! NSRegularExpression(pattern: #"^(?:回合|Round)[：:]\s*(\d+)"#)
        let handRe = try! NSRegularExpression(pattern: #"^(?:你的手牌|Your hand)[：:]\s*(.*)$"#)
        var seats: [String: Seat] = [:]
        var rows: [([Int], Int)?] = [nil, nil, nil, nil]
        var fields: [String: String] = [:]
        var hand: [String] = []
        var mustRow = false
        for raw in block.dropFirst() {
            let t = raw.trimmingCharacters(in: .whitespaces)
            if let m = match(seatRe, t), m.count >= 4 {
                seats[m[1]] = Seat(index: seats.count + 1, name: m[1], detail: "牛头 \(m[2]) · 手牌 \(m[3])")
            }
            if let m = match(rowRe, t), m.count >= 5 {
                let i = Int(m[1].isEmpty ? m[2] : m[1])! - 1
                if (0...3).contains(i) {
                    let nums = m[3].split(whereSeparator: { $0.isWhitespace }).compactMap { Int($0) }
                    rows[i] = (nums, Int(m[4]) ?? 0)
                }
            }
            if let m = match(roundRe, t), m.count >= 2 { fields["round"] = m[1] }
            if let m = match(handRe, t), m.count >= 2 {
                hand = m[1].split(whereSeparator: { $0.isWhitespace }).map(String.init).filter { Int($0) != nil }
            }
            if t.contains("必须选择一行") || t.lowercased().contains("must choose a row") { mustRow = true }
            let need = try! NSRegularExpression(pattern: #"^(\S+) (?:需要选行|must pick a row)"#)
            if let m = match(need, t), m.count >= 2, m[1] == myName { mustRow = true }
        }
        if mustRow { fields["mustRow"] = "1" }
        return GameSnapshot(
            kind: .niutou, state: state, header: block[0].trimmingCharacters(in: .whitespaces),
            seats: Array(seats.values), hand: hand,
            rows: rows.compactMap { $0 }, fields: fields,
            messages: Array(block.dropFirst().map { $0.trimmingCharacters(in: .whitespaces) }.filter {
                !$0.isEmpty && match(seatRe, $0) == nil && match(rowRe, $0) == nil
                    && match(roundRe, $0) == nil && match(handRe, $0) == nil
                    && !$0.hasPrefix("房主") && !$0.hasPrefix("Host")
            }.suffix(4))
        )
    }

    private static func parseMahjong(_ state: String, _ block: [String]) -> GameSnapshot {
        let seatRe = try! NSRegularExpression(pattern: #"^(东|南|西|北)家(?:（房主）)?[：:]\s*(\S+)(.*)$"#)
        let seatEn = try! NSRegularExpression(pattern: #"^(East|South|West|North)(?: \(host\))?[：:]\s*(\S+)(.*)$"#)
        let wallRe = try! NSRegularExpression(pattern: #"^(?:牌墙剩余|Wall left)[：:]\s*(\d+)"#)
        let discRe = try! NSRegularExpression(pattern: #"^(?:最近弃牌|Recent discards)[：:]\s*(.*)$"#)
        let meldRe = try! NSRegularExpression(pattern: #"^(?:你的副露|Your melds)[：:]\s*(.*)$"#)
        let handRe = try! NSRegularExpression(pattern: #"^(?:你的手牌|Your hand)[：:]\s*(.*)$"#)
        let tileRe = try! NSRegularExpression(pattern: #"\b([mps][1-9]|z[1-7])\b"#)
        var seats: [Seat] = []
        var fields: [String: String] = [:]
        var hand: [String] = []
        var discards: [String] = []
        var hints: [String] = []
        for raw in block.dropFirst() {
            let t = raw.trimmingCharacters(in: .whitespaces)
            if let m = match(seatRe, t) ?? match(seatEn, t), m.count >= 4 {
                let wind: String = {
                    switch m[1] {
                    case "East": return "东"; case "South": return "南"
                    case "West": return "西"; case "North": return "北"
                    default: return m[1]
                    }
                }()
                seats.append(Seat(
                    index: seats.count + 1, name: m[2],
                    detail: wind + "家" + (m[3].contains("[AI]") ? " · AI" : ""),
                    active: m[3].contains("<-")
                ))
                continue
            }
            if let m = match(wallRe, t), m.count >= 2 { fields["wall"] = m[1] }
            if let m = match(discRe, t), m.count >= 2 {
                discards = findAll(tileRe, m[1]).compactMap { $0.first }
            }
            if let m = match(meldRe, t), m.count >= 2 { fields["melds"] = m[1] }
            if let m = match(handRe, t), m.count >= 2 {
                hand = findAll(tileRe, m[1]).compactMap { $0.first }
            }
            if t.hasPrefix("你可用") || t.hasPrefix("You can") { fields["canAct"] = "turn" }
            if t.hasPrefix("当前可用") || t.contains("chi <") { fields["canAct"] = "claim" }
            if t.hasPrefix("你已") || t.hasPrefix("当前为响应") || t.hasPrefix("当前轮到") { hints.append(t) }
        }
        return GameSnapshot(
            kind: .mahjong, state: state, header: block[0].trimmingCharacters(in: .whitespaces),
            turnName: seats.first(where: { $0.active })?.name,
            seats: seats, hand: hand, community: discards, fields: fields,
            hints: Array(hints.suffix(1)),
            messages: Array(block.dropFirst().map { $0.trimmingCharacters(in: .whitespaces) }.filter {
                !$0.isEmpty && match(seatRe, $0) == nil && match(seatEn, $0) == nil
                    && match(wallRe, $0) == nil && match(discRe, $0) == nil
                    && match(handRe, $0) == nil && match(meldRe, $0) == nil
                    && !$0.hasPrefix("你可用") && !$0.hasPrefix("You can") && !$0.hasPrefix("当前可用")
            }.suffix(4))
        )
    }

    // MARK: - sanguo / werewolf

    static func sgsHandCards(_ text: String) -> [String] {
        let re = try! NSRegularExpression(pattern: #"(\d+)\.([^、\s]+)"#)
        return findAll(re, text).compactMap { $0.count >= 3 ? $0[2] : nil }
    }

    private static func parseSanguo(_ state: String, _ block: [String]) -> GameSnapshot {
        let seatRe = try! NSRegularExpression(pattern: #"^#(\d+)\s+(\S+)\s+(.*?)体力\s*(-?\d+)/(\d+)\s*手牌=(.*)$"#)
        let cardRe = try! NSRegularExpression(pattern: #"(\d+)\.([^、\s]+)"#)
        let handHead = try! NSRegularExpression(pattern: #"^──\s*你的手牌"#)
        var seats: [Int: Seat] = [:]
        var hand: [String] = []
        var fields: [String: String] = [:]
        var hints: [String] = []
        var turn: String?
        var expectHand = false
        var inHint = false
        for (i, raw) in block.enumerated() {
            let t = raw.trimmingCharacters(in: .whitespaces)
            if i == 0 {
                if let m = match(try! NSRegularExpression(pattern: #"牌堆(\d+)"#), t), m.count >= 2 {
                    fields["deck"] = m[1]
                }
                continue
            }
            if expectHand {
                expectHand = false
                if match(cardRe, t) != nil { hand = sgsHandCards(t); continue }
            }
            if match(handHead, t) != nil {
                expectHand = true
                if t.contains("（0张）") { hand = [] }
                continue
            }
            if let m = match(seatRe, t), m.count >= 7 {
                let idx = Int(m[1]) ?? 0
                let name = m[2].hasPrefix("▸") ? String(m[2].dropFirst()) : m[2]
                let mid = m[3].trimmingCharacters(in: .whitespaces)
                let handTxt = m[6].trimmingCharacters(in: .whitespaces)
                let mine = match(cardRe, handTxt) != nil
                if mine { hand = sgsHandCards(handTxt) }
                let hp = Int(m[4]) ?? 0
                seats[idx] = Seat(
                    index: idx, name: name,
                    detail: mid.replacingOccurrences(of: #"身份=\s*"#, with: "", options: .regularExpression)
                        .replacingOccurrences(of: #"\s+"#, with: " ", options: .regularExpression)
                        .trimmingCharacters(in: .whitespaces)
                        + (mine ? "" : "  手牌 \(handTxt.hasSuffix("张") ? String(handTxt.dropLast()) : handTxt)"),
                    alive: hp > 0 && !t.contains("阵亡"),
                    hp: hp, maxHp: Int(m[5])
                )
                continue
            }
            if let m = match(try! NSRegularExpression(pattern: #"(?:你的身份|Your role)[：:]\s*(\S+)"#), t), m.count >= 2 {
                fields["role"] = m[1]
            }
            if let m = match(try! NSRegularExpression(pattern: #"当前回合[：:]\s*#\d+\s*(\S+)"#), t), m.count >= 2 {
                turn = m[1]
            }
            if let m = match(try! NSRegularExpression(pattern: #"轮到\s*#\d+\s*(\S+)\s*的回合"#), t), m.count >= 2 {
                turn = m[1]
            }
            if t.hasPrefix("▸") {
                inHint = true
                hints = [t]
                continue
            }
            if inHint && (t.hasPrefix("牌顶") || raw.hasPrefix("     ")) {
                hints.append(t)
                continue
            }
            inHint = false
            if t.hasPrefix("需") || t.contains("响应") || t.contains("请出") || t.contains("是否") {
                hints.append(t)
            }
        }
        return GameSnapshot(
            kind: .sanguo, state: state, header: block[0].trimmingCharacters(in: .whitespaces),
            turnName: turn,
            seats: seats.values.sorted { $0.index < $1.index }.map {
                var s = $0; s.active = s.name == turn; return s
            },
            hand: hand, fields: fields, hints: Array(hints.suffix(8)),
            messages: Array(block.dropFirst().map { $0.trimmingCharacters(in: .whitespaces) }.filter {
                !$0.isEmpty && match(seatRe, $0) == nil && !$0.hasPrefix("座次")
                    && !$0.hasPrefix("#") && !$0.hasPrefix("距离") && !$0.hasPrefix("当前回合")
                    && !$0.hasPrefix("你的") && !$0.hasPrefix("──") && !$0.hasPrefix("指令详情")
                    && !$0.hasPrefix("武器") && !$0.hasPrefix("▸") && !$0.hasPrefix("牌顶")
            }.suffix(4))
        )
    }

    private static func parseWerewolf(_ state0: String, _ block: [String]) -> GameSnapshot {
        var state = state0
        var round = ""
        var alive: [String] = []
        var joined = Set<String>()
        var votes = ""
        for raw in block {
            let t = raw.trimmingCharacters(in: .whitespaces)
            if let m = match(try! NSRegularExpression(pattern: #"^werewolf state:\s*(\S+)"#), t), m.count >= 2 {
                state = m[1]
            }
            if let m = match(try! NSRegularExpression(pattern: #"^round:\s*(\d+)"#), t), m.count >= 2 {
                round = m[1]
            }
            if let m = match(try! NSRegularExpression(pattern: #"^Alive:\s*(.*)$"#), t), m.count >= 2 {
                alive = m[1].split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
            }
            if let m = match(try! NSRegularExpression(pattern: #"^(\S+) joined werewolf"#), t), m.count >= 2 {
                joined.insert(m[1])
            }
            if let m = match(try! NSRegularExpression(pattern: #"^(Day|Night) (\d+)(?: begins)?\."#), t), m.count >= 3 {
                state = m[1].lowercased(); round = m[2]
            }
            if let m = match(try! NSRegularExpression(pattern: #"^Werewolf started\. Night (\d+)"#), t), m.count >= 2 {
                state = "night"; round = m[1]
            }
            if let m = match(try! NSRegularExpression(pattern: #"^votes:\s*(\S+)"#), t), m.count >= 2 {
                votes = m[1]
            }
            if t.contains(" voted "),
               let m = match(try! NSRegularExpression(pattern: #"\((\d+/\d+)\)$"#), t), m.count >= 2 {
                votes = m[1]
            }
            if let m = match(try! NSRegularExpression(pattern: #"^Voted out:\s*(\S+)"#), t), m.count >= 2 {
                alive.removeAll { $0 == m[1] }
            }
            if let m = match(try! NSRegularExpression(pattern: #"^Night deaths:\s*(.*)$"#), t), m.count >= 2 {
                let dead = Set(m[1].split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) })
                alive = alive.filter { !dead.contains($0) }
            }
            if t == "Villagers win." || t == "Wolves win." { state = "ended" }
        }
        if alive.isEmpty, !joined.isEmpty { alive = Array(joined) }
        var fields: [String: String] = [:]
        if !round.isEmpty { fields["round"] = round }
        if !votes.isEmpty { fields["votes"] = votes }
        return GameSnapshot(
            kind: .werewolf, state: state, header: block[0].trimmingCharacters(in: .whitespaces),
            seats: alive.enumerated().map { Seat(index: $0.offset + 1, name: $0.element, detail: "") },
            fields: fields,
            messages: Array(block.dropFirst().map { $0.trimmingCharacters(in: .whitespaces) }.filter {
                !$0.isEmpty && !$0.hasPrefix("werewolf state") && !$0.hasPrefix("round:") && !$0.hasPrefix("Alive:")
            }.suffix(4))
        )
    }
}
