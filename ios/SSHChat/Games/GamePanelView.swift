import SwiftUI
import Combine

/// 折叠式游戏 UI：解析 [*] 文本 → 棋盘/手牌 → 点击发 /game move。
@MainActor
final class GameBoardModel: ObservableObject {
    @Published var visible = false
    @Published var maximized = false
    @Published var snapshot: GameSnapshot?
    @Published var selected: (Int, Int)?
    @Published var highlights: Set<String> = []
    @Published var statusText = "当前房间没有可识别的对局"
    @Published var msgText = ""
    @Published var selectedHand: String?
    @Published var selectedTarget: String?
    @Published var battleshipOrientH = true
    @Published var toast: String?

    var myName = ""
    var onSend: ((String) -> Void)?

    private var buffer: [String] = []
    private var pendingStone: (Int, Int)?
    private var battleshipShipIdx = 0
    private var junqiPieceIdx = 0

    private let battleshipShips: [(String, Int)] = [
        ("carrier", 5), ("battleship", 4), ("cruiser", 3), ("submarine", 3), ("destroyer", 2),
    ]
    private let junqiPieces = [
        "flag", "commander", "army", "division", "division",
        "brigade", "brigade", "regiment", "regiment",
        "battalion", "battalion", "company", "company", "company",
        "platoon", "platoon", "platoon", "engineer", "engineer", "engineer",
        "mine", "mine", "mine", "bomb", "bomb",
    ]

    func setVisible(_ show: Bool) {
        visible = show
        if !show { maximized = false }
        if show {
            send("/game show")
        }
    }

    func toggleMaximized() {
        maximized.toggle()
    }

    func toggle() {
        setVisible(!visible)
    }

    func clear() {
        buffer.removeAll()
        snapshot = nil
        selected = nil
        pendingStone = nil
        selectedHand = nil
        selectedTarget = nil
        highlights.removeAll()
        maximized = false
        statusText = "当前房间没有可识别的对局"
        msgText = ""
    }

    func feedLine(_ text: String) {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty else { return }
        if buffer.count >= 240 {
            buffer.removeFirst(min(40, buffer.count))
        }
        buffer.append(t)
        guard let parsed = GameParser.parseLatest(lines: buffer, myName: myName) else { return }
        snapshot = parsed
        if visible {
            refreshSelection()
            updateStatus(parsed)
        } else if GameParser.detectHeader(t) != nil {
            statusText = "棋盘 · \(parsed.kind.title)"
        }
    }

    private func refreshSelection() {
        selected = nil
        pendingStone = nil
        highlights.removeAll()
        if let s = snapshot, s.kind == .reversi, let b = s.board {
            for r in 0..<b.rows {
                for c in 0..<b.cols where b.at(r, c).isEmpty {
                    highlights.insert("\(r),\(c)")
                }
            }
        }
        updateStatus(snapshot)
    }

    private func updateStatus(_ s: GameSnapshot?) {
        guard let s else {
            statusText = "当前房间没有可识别的对局。可用 /game new <名称> 开局。"
            msgText = ""
            return
        }
        statusText = buildStatus(s)
        msgText = s.messages.joined(separator: " · ").ifEmpty {
            s.info.joined(separator: " · ").ifEmpty { s.hints.joined(separator: " · ") }
        }
    }

    private func buildStatus(_ s: GameSnapshot) -> String {
        var parts: [String] = ["\(s.kind.title) · \(s.state)"]
        let seats = s.players.map { "\(sideZh($0.key))\($0.value)" }.joined(separator: "  ")
        if !seats.isEmpty { parts.append(seats) }
        if let n = s.turnName { parts.append("轮到 \(n)") }
        else if let side = s.turnSide { parts.append("轮到 \(sideZh(side))") }
        if let pot = s.fields["pot"] { parts.append("底池 \(pot)") }
        if let stage = s.fields["stage"] { parts.append(stage) }
        if let role = s.fields["role"] { parts.append("身份 \(role)") }
        if let round = s.fields["round"] { parts.append("第 \(round) 轮") }
        return parts.joined(separator: "  ·  ")
    }

    private func sideZh(_ side: String) -> String {
        switch side {
        case "red": return "红:"
        case "black": return "黑:"
        case "white": return "白:"
        case "blue": return "蓝:"
        case "p1": return "P1:"
        case "p2": return "P2:"
        default: return "\(side):"
        }
    }

    func boardStyle(for kind: GameKind) -> BoardStyle {
        switch kind {
        case .xiangqi: return .xiangqi
        case .chess: return .chess
        case .go, .gomoku, .reversi: return .stones
        case .junqi: return .junqi
        case .battleship: return .ship
        case .doushou: return .doushou
        default: return .grid
        }
    }

    // MARK: - taps

    func onBoardTap(r: Int, c: Int, primary: Bool) {
        guard let s = snapshot else { return }
        switch s.kind {
        case .go, .gomoku, .reversi: tapStone(s, r, c)
        case .chess: tapChess(s, r, c)
        case .xiangqi, .doushou, .junqi, .darkchess: tapMovePiece(s, r, c)
        case .battleship: tapBattleship(s, r, c, primary: primary)
        default: break
        }
    }

    private func tapStone(_ s: GameSnapshot, _ r: Int, _ c: Int) {
        guard let b = s.board else { return }
        if !b.at(r, c).isEmpty && s.kind != .reversi {
            toast = "该点已有棋子"
            return
        }
        let srv = displayToServer(s, r, c)
        pendingStone = nil
        selected = nil
        sendMove("\(srv.0) \(srv.1)")
    }

    private func tapChess(_ s: GameSnapshot, _ r: Int, _ c: Int) {
        guard let b = s.board else { return }
        let files = b.colLabels.isEmpty ? ["a", "b", "c", "d", "e", "f", "g", "h"] : b.colLabels
        let ranks = b.rowLabels.isEmpty ? Array((1...8).reversed()) : b.rowLabels
        guard r < ranks.count, c < files.count else { return }
        let sq = "\(files[c])\(ranks[r])"
        if let from = selected {
            let fromSq = "\(files[from.1])\(ranks[from.0])"
            selected = nil
            if fromSq != sq { sendMove("\(fromSq)\(sq)") }
        } else {
            guard !b.at(r, c).isEmpty else { return }
            selected = (r, c)
        }
    }

    private func tapMovePiece(_ s: GameSnapshot, _ r: Int, _ c: Int) {
        guard let b = s.board else { return }
        let srv = displayToServer(s, r, c)
        if s.kind == .darkchess {
            let tok = b.at(r, c)
            if selected == nil {
                if tok == "?" {
                    sendMove("flip \(srv.0) \(srv.1)")
                    return
                }
                guard !tok.isEmpty else { return }
                selected = (r, c)
                return
            }
            let fr = selected!.0, fc = selected!.1
            let fromSrv = displayToServer(s, fr, fc)
            selected = nil
            if fr == r && fc == c { return }
            sendMove("move \(fromSrv.0) \(fromSrv.1) \(srv.0) \(srv.1)")
            return
        }
        if s.isSetup && s.kind == .junqi {
            guard junqiPieceIdx < junqiPieces.count else { return }
            sendMove("setup \(junqiPieces[junqiPieceIdx]) \(srv.0) \(srv.1)")
            junqiPieceIdx = min(junqiPieceIdx + 1, junqiPieces.count - 1)
            return
        }
        if selected == nil {
            let tok = b.at(r, c)
            guard !tok.isEmpty, tok != "?" else { return }
            selected = (r, c)
        } else {
            let fr = selected!.0, fc = selected!.1
            let fromSrv = displayToServer(s, fr, fc)
            selected = nil
            if fr == r && fc == c { return }
            switch s.kind {
            case .xiangqi:
                sendMove("coord \(fromSrv.0) \(fromSrv.1) \(srv.0) \(srv.1)")
            case .junqi:
                sendMove("move \(fromSrv.0) \(fromSrv.1) \(srv.0) \(srv.1)")
            default:
                sendMove("\(fromSrv.0) \(fromSrv.1) \(srv.0) \(srv.1)")
            }
        }
    }

    private func tapBattleship(_ s: GameSnapshot, _ r: Int, _ c: Int, primary: Bool) {
        let row = r + 1, col = c + 1
        if s.isSetup {
            guard primary else { toast = "布置阶段请点己方海域"; return }
            guard battleshipShipIdx < battleshipShips.count else {
                toast = "舰船已全部布置，点准备"
                return
            }
            let ship = battleshipShips[battleshipShipIdx].0
            let ori = battleshipOrientH ? "h" : "v"
            sendMove("place \(ship) \(row) \(col) \(ori)")
            battleshipShipIdx = min(battleshipShipIdx + 1, battleshipShips.count)
            return
        }
        if s.isPlaying {
            guard !primary else { toast = "开火请点对方海域"; return }
            sendMove("fire \(row) \(col)")
        }
    }

    private func displayToServer(_ s: GameSnapshot, _ r: Int, _ c: Int) -> (Int, Int) {
        guard let b = s.board else { return (r + 1, c + 1) }
        if s.kind == .xiangqi {
            let rr = s.flipped ? b.rows - r : r + 1
            let cc = s.flipped ? b.cols - c : c + 1
            return (rr, cc)
        }
        let row = (r < b.rowLabels.count) ? b.rowLabels[r] : r + 1
        let col: Int = {
            if c < b.colLabels.count { return Int(b.colLabels[c]) ?? (c + 1) }
            return c + 1
        }()
        return (row, col)
    }

    // MARK: - actions

    func sendMove(_ payload: String) { send("/game move \(payload)") }
    func send(_ cmd: String) { onSend?(cmd) }

    func autoPlaceBattleship() {
        ["place carrier 1 1 h", "place battleship 3 1 h", "place cruiser 5 1 h",
         "place submarine 7 1 h", "place destroyer 9 1 h"].forEach { sendMove($0) }
        battleshipShipIdx = battleshipShips.count
        toast = "已发送标准布置，点准备"
    }

    func autoSetupJunqi() {
        let pieces = [
            "commander", "flag", "army", "division", "division",
            "bomb", "bomb", "brigade", "brigade", "regiment",
            "regiment", "battalion", "battalion", "company", "company",
            "mine", "mine", "mine", "company", "platoon",
            "platoon", "platoon", "engineer", "engineer", "engineer",
        ]
        var i = 0
        for row in 1...5 {
            for col in 1...5 {
                guard i < pieces.count else { return }
                sendMove("setup \(pieces[i]) \(row) \(col)")
                i += 1
            }
        }
        toast = "已发送标准布阵"
    }

    func afterSgs() {
        selectedHand = nil
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { [weak self] in
            self?.send("/game show")
        }
    }

    func afterWolf() {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { [weak self] in
            self?.send("/game show")
        }
    }
}

private extension String {
    func ifEmpty(_ alt: () -> String) -> String { isEmpty ? alt() : self }
}

// MARK: - View

struct GamePanelView: View {
    @ObservedObject var model: GameBoardModel
    var fullscreen: Bool = false
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(model.snapshot.map { "\($0.kind.title) · \($0.state)" } ?? "棋盘")
                    .font(.subheadline.weight(.bold))
                    .foregroundColor(Color(red: 0.106, green: 0.369, blue: 0.125))
                Spacer()
                Button(fullscreen ? "还原" : "最大化") {
                    if fullscreen {
                        model.maximized = false
                        dismiss()
                    } else {
                        model.maximized = true
                    }
                }
                .font(.subheadline.weight(.semibold))
                .foregroundColor(Color(red: 0.106, green: 0.369, blue: 0.125))
                .frame(minWidth: 56, minHeight: 44)
                .contentShape(Rectangle())
                if !fullscreen {
                    Button("收起") { model.setVisible(false) }
                        .font(.caption)
                        .foregroundColor(Color(red: 0.106, green: 0.369, blue: 0.125))
                }
            }
            Text(model.statusText)
                .font(.caption2)
                .foregroundColor(.secondary)
                .lineLimit(2)
            if let s = model.snapshot {
                content(s)
            } else {
                lobbyActions
            }
            if !model.msgText.isEmpty {
                Text(model.msgText)
                    .font(.caption2)
                    .foregroundColor(.secondary)
                    .lineLimit(3)
            }
        }
        .padding(fullscreen ? 12 : 8)
        .frame(maxWidth: .infinity, maxHeight: fullscreen ? .infinity : nil, alignment: .top)
        .background(fullscreen ? Color.white : Color(white: 0.98))
        .clipShape(RoundedRectangle(cornerRadius: fullscreen ? 0 : 10))
        .overlay(
            RoundedRectangle(cornerRadius: fullscreen ? 0 : 10)
                .stroke(fullscreen ? Color.clear : Color(white: 0.9), lineWidth: 1)
        )
    }

    @ViewBuilder
    private func content(_ s: GameSnapshot) -> some View {
        switch s.kind {
        case .xiangqi, .chess, .go, .gomoku, .doushou, .junqi, .darkchess, .reversi, .battleship:
            boardGame(s)
        case .holdem, .zjh:
            poker(s)
        case .niutou:
            niutou(s)
        case .mahjong:
            mahjong(s)
        case .sanguo:
            sanguo(s)
        case .werewolf:
            werewolf(s)
        }
    }

    // MARK: board

    private func boardGame(_ s: GameSnapshot) -> some View {
        let big = fullscreen
        return VStack(alignment: .leading, spacing: 6) {
            BoardCanvasView(
                board: s.board,
                style: model.boardStyle(for: s.kind),
                selected: model.selected,
                highlights: model.highlights,
                expanded: big,
                onTap: { r, c in model.onBoardTap(r: r, c: c, primary: true) }
            )
            .frame(maxWidth: .infinity, maxHeight: big ? .infinity : 280)
            if s.kind == .battleship, let b2 = s.board2 {
                Text(s.isSetup ? "对方海域（布置阶段不可开火）" : "对方海域（点格子开火）")
                    .font(.caption2).foregroundColor(.secondary)
                BoardCanvasView(
                    board: b2,
                    style: .ship,
                    selected: nil,
                    highlights: [],
                    expanded: big,
                    onTap: { r, c in model.onBoardTap(r: r, c: c, primary: false) }
                )
                .frame(maxWidth: .infinity, maxHeight: big ? 240 : 160)
            }
            actionScroll {
                commonActions(s)
                if s.isWaiting {
                    chip("加入") { model.send("/game join") }
                }
                if s.isSetup && s.kind == .battleship {
                    chip(model.battleshipOrientH ? "方向:横" : "方向:竖") {
                        model.battleshipOrientH.toggle()
                    }
                    chip("一键布置") { model.autoPlaceBattleship() }
                    chip("准备") { model.sendMove("ready") }
                }
                if s.isSetup && s.kind == .junqi {
                    chip("一键布阵") { model.autoSetupJunqi() }
                    chip("准备") { model.sendMove("ready") }
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: big ? .infinity : nil, alignment: .top)
    }

    // MARK: poker

    private func poker(_ s: GameSnapshot) -> some View {
        let concealed = s.fields["concealed"] == "1"
        return VStack(alignment: .leading, spacing: 6) {
            if !s.seats.isEmpty { seatBar(s.seats) }
            if !s.community.isEmpty {
                Text("公共牌").font(.caption2)
                pokerCardRow(s.community)
            }
            Text(concealed ? "手牌（闷牌）" : "手牌").font(.caption.weight(.semibold))
            if concealed {
                pokerBackRow(count: 3)
            } else if !s.hand.isEmpty {
                pokerCardRow(s.hand)
            }
            actionScroll {
                commonActions(s)
                if s.isWaiting {
                    chip("开始") { model.sendMove("start") }
                    chip("加入") { model.send("/game join") }
                }
                if s.isPlaying {
                    if s.kind == .zjh {
                        chip("看牌") { model.sendMove("look") }
                        chip("跟注") { model.sendMove("follow") }
                        chip("加注") { model.sendMove("raise") }
                        chip("比牌") {
                            if let t = model.selectedTarget { model.sendMove("compare \(t)") }
                            else { model.toast = "先选目标" }
                        }
                    } else {
                        chip("过牌") { model.sendMove("check") }
                        chip("跟注") { model.sendMove("call") }
                        chip("加注") { model.sendMove("raise") }
                        chip("全下") { model.sendMove("allin") }
                    }
                    chip("弃牌") { model.sendMove("fold") }
                }
            }
        }
    }

    private func pokerCardRow(_ cards: [String]) -> some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                ForEach(Array(cards.enumerated()), id: \.offset) { _, c in
                    let red = c.contains("红") || c.contains("方") || c.contains("♥") || c.contains("♦")
                    Text(c)
                        .font(.system(size: 15, weight: .bold, design: .rounded))
                        .foregroundColor(red ? Color(red: 0.78, green: 0.16, blue: 0.16) : .primary)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 12)
                        .background(Color.white)
                        .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.gray.opacity(0.45), lineWidth: 1))
                        .cornerRadius(6)
                        .shadow(color: .black.opacity(0.08), radius: 1, y: 1)
                }
            }
        }
    }

    private func pokerBackRow(count: Int) -> some View {
        HStack(spacing: 6) {
            ForEach(0..<count, id: \.self) { _ in
                Text("🂠")
                    .font(.system(size: 18))
                    .foregroundColor(Color(red: 0.91, green: 0.92, blue: 0.96))
                    .padding(.horizontal, 12)
                    .padding(.vertical, 12)
                    .background(Color(red: 0.10, green: 0.14, blue: 0.49))
                    .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color(red: 0.22, green: 0.29, blue: 0.67), lineWidth: 1))
                    .cornerRadius(6)
            }
        }
    }

    private func niutou(_ s: GameSnapshot) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            if !s.seats.isEmpty { seatBar(s.seats) }
            if !s.hand.isEmpty {
                Text("手牌").font(.caption.weight(.semibold))
                cardRow(s.hand, selectable: true)
            }
            actionScroll {
                commonActions(s)
                if s.isWaiting {
                    chip("开始") { model.sendMove("start") }
                    chip("加入") { model.send("/game join") }
                }
                if s.isPlaying {
                    chip("出牌") {
                        if let h = model.selectedHand { model.sendMove("play \(h)"); model.selectedHand = nil }
                        else { model.toast = "先选手牌" }
                    }
                    ForEach(1...4, id: \.self) { row in
                        chip("选行\(row)") { model.sendMove("row \(row)") }
                    }
                }
            }
        }
    }

    // MARK: mahjong

    private func mahjong(_ s: GameSnapshot) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            if !s.seats.isEmpty { seatBar(s.seats) }
            if !s.community.isEmpty {
                Text("弃牌").font(.caption2)
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 4) {
                        ForEach(Array(s.community.suffix(8).enumerated()), id: \.offset) { _, t in
                            Text(t).font(.caption2.monospaced()).padding(4)
                                .background(Color.gray.opacity(0.15)).cornerRadius(4)
                        }
                    }
                }
            }
            if let melds = s.fields["melds"] {
                Text("副露 \(melds)").font(.caption2).foregroundColor(.secondary)
            }
            if !s.hand.isEmpty {
                Text("手牌").font(.caption.weight(.semibold))
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 4) {
                        ForEach(Array(s.hand.enumerated()), id: \.offset) { _, t in
                            Button {
                                model.selectedHand = model.selectedHand == t ? nil : t
                            } label: {
                                Text(t)
                                    .font(.caption.weight(.bold).monospaced())
                                    .padding(.horizontal, 8).padding(.vertical, 6)
                                    .background(model.selectedHand == t ? Color.green.opacity(0.25) : Color.white)
                                    .overlay(RoundedRectangle(cornerRadius: 6).stroke(
                                        model.selectedHand == t ? Color.green : Color.gray.opacity(0.35)))
                                    .cornerRadius(6)
                            }
                        }
                    }
                }
            }
            actionScroll {
                commonActions(s)
                if s.isWaiting {
                    chip("开始") { model.sendMove("start") }
                    chip("加入") { model.send("/game join") }
                }
                switch s.fields["canAct"] {
                case "turn":
                    chip("出牌") {
                        if let h = model.selectedHand { model.sendMove("discard \(h)"); model.selectedHand = nil }
                        else { model.toast = "先选手牌" }
                    }
                    chip("杠") {
                        if let h = model.selectedHand { model.sendMove("gang \(h)") }
                        else { model.sendMove("gang") }
                    }
                    chip("胡") { model.sendMove("hu") }
                case "claim":
                    chip("碰") { model.sendMove("peng") }
                    chip("杠") { model.sendMove("gang") }
                    chip("胡") { model.sendMove("hu") }
                    chip("过") { model.sendMove("pass") }
                default: EmptyView()
                }
            }
        }
    }

    // MARK: sanguo

    private func sanguo(_ s: GameSnapshot) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            if !s.seats.isEmpty { seatBar(s.seats) }
            if !s.hints.isEmpty {
                Text(s.hints.joined(separator: "\n"))
                    .font(.caption2).foregroundColor(.secondary).lineLimit(4)
            }
            if !s.hand.isEmpty {
                Text("手牌").font(.caption.weight(.semibold))
                cardRow(s.hand, selectable: true)
            }
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 4) {
                    Text("目标:").font(.caption2)
                    ForEach(s.seats.filter { $0.name != model.myName && $0.alive }) { seat in
                        chip(seat.name, selected: model.selectedTarget == seat.name) {
                            model.selectedTarget = seat.name
                        }
                    }
                }
            }
            actionScroll {
                commonActions(s)
                if s.isWaiting {
                    chip("开始") { model.sendMove("开始"); model.send("/game show") }
                    chip("加入") { model.send("/game join") }
                }
                if s.isPlaying {
                    chip("杀") {
                        guard let t = model.selectedTarget else { model.toast = "先选目标"; return }
                        if let c = model.selectedHand { model.sendMove("杀 \(t) \(c)") }
                        else { model.sendMove("杀 \(t)") }
                        model.afterSgs()
                    }
                    chip("闪") { model.sendMove("闪"); model.afterSgs() }
                    chip("桃") { model.sendMove("桃"); model.afterSgs() }
                    chip("决斗") {
                        guard let t = model.selectedTarget else { model.toast = "先选目标"; return }
                        model.sendMove("决斗 \(t)"); model.afterSgs()
                    }
                    chip("过") { model.sendMove("过"); model.afterSgs() }
                    chip("武将") { model.sendMove("武将") }
                }
            }
        }
    }

    // MARK: werewolf

    private func werewolf(_ s: GameSnapshot) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            if !s.seats.isEmpty { seatBar(s.seats) }
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 4) {
                    ForEach(s.seats) { seat in
                        chip(seat.name, selected: model.selectedTarget == seat.name) {
                            model.selectedTarget = seat.name
                        }
                    }
                }
            }
            actionScroll {
                commonActions(s)
                if s.isWaiting {
                    chip("开始") { model.sendMove("start"); model.afterWolf() }
                    chip("加入") { model.send("/game join") }
                }
                switch s.state.lowercased() {
                case "night":
                    chip("刀杀") {
                        guard let t = model.selectedTarget else { model.toast = "先选目标"; return }
                        model.sendMove("kill \(t)"); model.afterWolf()
                    }
                    chip("查验") {
                        guard let t = model.selectedTarget else { model.toast = "先选目标"; return }
                        model.sendMove("check \(t)"); model.afterWolf()
                    }
                    chip("救人") { model.sendMove("save"); model.afterWolf() }
                    chip("毒人") {
                        guard let t = model.selectedTarget else { model.toast = "先选目标"; return }
                        model.sendMove("poison \(t)"); model.afterWolf()
                    }
                    chip("过") { model.sendMove("pass"); model.afterWolf() }
                case "day":
                    chip("投票") {
                        guard let t = model.selectedTarget else { model.toast = "先选目标"; return }
                        model.sendMove("vote \(t)"); model.afterWolf()
                    }
                default: EmptyView()
                }
            }
        }
    }

    // MARK: lobby

    private var lobbyActions: some View {
        let games: [(String, String)] = [
            ("xiangqi", "象棋"), ("chess", "国际象棋"), ("go", "围棋"), ("gomoku", "五子"),
            ("doushou", "斗兽"), ("junqi", "军棋"), ("darkchess", "暗棋"), ("reversi", "黑白"),
            ("battleship", "海战"), ("holdem", "德州"), ("zjh", "金花"), ("niutou", "牛头"),
            ("mahjong", "麻将"), ("sanguo", "三国杀"), ("werewolf", "狼人"),
        ]
        return actionScroll {
            ForEach(games, id: \.0) { id, label in
                chip(label) { model.send("/game new \(id)") }
            }
            chip("列表") { model.send("/game list") }
            chip("加入") { model.send("/game join") }
        }
    }

    // MARK: helpers

    @ViewBuilder
    private func commonActions(_ s: GameSnapshot) -> some View {
        chip("刷新") { model.send("/game show") }
        chip("席位") { model.send("/game seats") }
        if s.isPlaying { chip("认负") { model.send("/game resign") } }
        chip("结束") { model.send("/game end") }
        if s.kind == .go || s.kind == .reversi {
            chip("停一手") { model.sendMove("pass") }
        }
    }

    private func seatBar(_ seats: [Seat]) -> some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                ForEach(seats) { seat in
                    VStack(alignment: .leading, spacing: 1) {
                        Text("\(seat.index).\(seat.name)")
                            .font(.caption2.weight(seat.active ? .bold : .regular))
                            .foregroundColor(seat.alive ? (seat.active ? .green : .primary) : .red)
                        if !seat.detail.isEmpty {
                            Text(seat.detail).font(.caption2).foregroundColor(.secondary).lineLimit(1)
                        }
                    }
                    .padding(4)
                    .background(seat.active ? Color.green.opacity(0.12) : Color.clear)
                    .cornerRadius(4)
                }
            }
        }
    }

    private func cardRow(_ cards: [String], selectable: Bool) -> some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 4) {
                ForEach(Array(cards.enumerated()), id: \.offset) { _, c in
                    Button {
                        guard selectable else { return }
                        model.selectedHand = model.selectedHand == c ? nil : c
                    } label: {
                        Text(c)
                            .font(.caption.monospaced())
                            .padding(6)
                            .background(model.selectedHand == c ? Color.green.opacity(0.25) : Color.white)
                            .overlay(RoundedRectangle(cornerRadius: 4).stroke(Color.gray.opacity(0.4)))
                            .cornerRadius(4)
                    }
                    .disabled(!selectable)
                }
            }
        }
    }

    private func actionScroll<Content: View>(@ViewBuilder content: () -> Content) -> some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) { content() }
        }
    }

    private func chip(_ title: String, selected: Bool = false, action: @escaping () -> Void) -> some View {
        Button(title, action: action)
            .font(.caption)
            .buttonStyle(.bordered)
            .controlSize(.small)
            .tint(selected ? .green : .primary)
    }
}

/// 真正全屏棋盘：盖住聊天；保留顶部安全区，避免「还原」落在刘海点不到。
struct GamePanelFullscreenView: View {
    @ObservedObject var model: GameBoardModel

    var body: some View {
        GamePanelView(model: model, fullscreen: true)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
            .background(Color.white.ignoresSafeArea())
            .preferredColorScheme(.light)
    }
}
