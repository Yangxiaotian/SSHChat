import SwiftUI

enum BoardStyle {
    case grid, stones, xiangqi, chess, junqi, ship, doushou
}

struct BoardCanvasView: View {
    var board: Board?
    var style: BoardStyle = .grid
    var selected: (Int, Int)? = nil
    var highlights: Set<String> = []
    var expanded: Bool = false
    var onTap: ((Int, Int) -> Void)?

    var body: some View {
        GeometryReader { geo in
            let b = board
            let pad: CGFloat = 8
            let layout: (cell: CGFloat, ox: CGFloat, oy: CGFloat) = {
                guard let b, b.cols > 0, b.rows > 0 else { return (20, pad, pad) }
                if style == .xiangqi, b.cols > 1, b.rows > 1 {
                    let cell = min((geo.size.width - pad * 2) / CGFloat(b.cols - 1),
                                   (geo.size.height - pad * 2) / CGFloat(b.rows - 1))
                    let ox = (geo.size.width - cell * CGFloat(b.cols - 1)) / 2
                    let oy = (geo.size.height - cell * CGFloat(b.rows - 1)) / 2
                    return (cell, ox, oy)
                }
                let cell = min((geo.size.width - pad * 2) / CGFloat(b.cols),
                               (geo.size.height - pad * 2) / CGFloat(b.rows))
                let ox = (geo.size.width - cell * CGFloat(b.cols)) / 2
                let oy = (geo.size.height - cell * CGFloat(b.rows)) / 2
                return (cell, ox, oy)
            }()
            let cell = layout.cell
            let ox = layout.ox
            let oy = layout.oy

            Canvas { ctx, _ in
                guard let b else { return }
                switch style {
                case .xiangqi: drawXiangqi(ctx, b, ox, oy, cell)
                case .stones: drawStones(ctx, b, ox, oy, cell)
                case .chess: drawChess(ctx, b, ox, oy, cell)
                case .junqi: drawJunqi(ctx, b, ox, oy, cell)
                case .ship: drawShip(ctx, b, ox, oy, cell)
                case .doushou: drawDoushou(ctx, b, ox, oy, cell)
                case .grid: drawGrid(ctx, b, ox, oy, cell)
                }
                if let s = selected, s.0 >= 0, s.0 < b.rows, s.1 >= 0, s.1 < b.cols {
                    if style == .xiangqi {
                        let cx = ox + CGFloat(s.1) * cell
                        let cy = oy + CGFloat(s.0) * cell
                        ctx.stroke(Path(ellipseIn: CGRect(x: cx - cell * 0.42, y: cy - cell * 0.42,
                                                          width: cell * 0.84, height: cell * 0.84)),
                                   with: .color(.green), lineWidth: 3)
                    } else {
                        let r = CGRect(x: ox + CGFloat(s.1) * cell, y: oy + CGFloat(s.0) * cell,
                                       width: cell, height: cell)
                        ctx.stroke(Path(r), with: .color(.green), lineWidth: 3)
                    }
                }
                for key in highlights {
                    let parts = key.split(separator: ",")
                    guard parts.count == 2, let r = Int(parts[0]), let c = Int(parts[1]),
                          r >= 0, r < b.rows, c >= 0, c < b.cols else { continue }
                    if style == .xiangqi {
                        let cx = ox + CGFloat(c) * cell
                        let cy = oy + CGFloat(r) * cell
                        ctx.fill(Path(ellipseIn: CGRect(x: cx - cell * 0.2, y: cy - cell * 0.2,
                                                        width: cell * 0.4, height: cell * 0.4)),
                                 with: .color(.green.opacity(0.35)))
                    } else {
                        let rect = CGRect(x: ox + CGFloat(c) * cell, y: oy + CGFloat(r) * cell,
                                          width: cell, height: cell)
                        ctx.fill(Path(rect), with: .color(.green.opacity(0.25)))
                    }
                }
            }
            .contentShape(Rectangle())
            .gesture(DragGesture(minimumDistance: 0).onEnded { value in
                guard let b, cell > 0 else { return }
                let (r, c): (Int, Int) = {
                    if style == .xiangqi {
                        let rr = Int((value.location.y - oy) / cell + 0.5)
                        let cc = Int((value.location.x - ox) / cell + 0.5)
                        return (min(max(rr, 0), b.rows - 1), min(max(cc, 0), b.cols - 1))
                    }
                    return (Int((value.location.y - oy) / cell), Int((value.location.x - ox) / cell))
                }()
                if r >= 0, r < b.rows, c >= 0, c < b.cols {
                    onTap?(r, c)
                }
            })
        }
        .frame(minHeight: expanded ? 280 : 160, maxHeight: expanded ? .infinity : 280)
        .background(Color(white: 0.97))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    // MARK: draw helpers

    private func drawXiangqi(_ ctx: GraphicsContext, _ b: Board, _ ox: CGFloat, _ oy: CGFloat, _ cell: CGFloat) {
        let margin = cell * 0.55
        let bg = CGRect(x: ox - margin, y: oy - margin,
                        width: CGFloat(b.cols - 1) * cell + margin * 2,
                        height: CGFloat(b.rows - 1) * cell + margin * 2)
        ctx.fill(Path(bg), with: .color(Color(red: 0.87, green: 0.72, blue: 0.53)))
        let lineColor = Color(red: 0.36, green: 0.25, blue: 0.22)

        // 横线
        for r in 0..<b.rows {
            var p = Path()
            let y = oy + CGFloat(r) * cell
            p.move(to: CGPoint(x: ox, y: y))
            p.addLine(to: CGPoint(x: ox + CGFloat(b.cols - 1) * cell, y: y))
            ctx.stroke(p, with: .color(lineColor), lineWidth: r == 0 || r == b.rows - 1 ? 2 : 1)
        }
        // 竖线（河界断开）
        for c in 0..<b.cols {
            let x = ox + CGFloat(c) * cell
            let thick: CGFloat = (c == 0 || c == b.cols - 1) ? 2 : 1
            if c == 0 || c == b.cols - 1 || b.rows < 10 {
                var p = Path()
                p.move(to: CGPoint(x: x, y: oy))
                p.addLine(to: CGPoint(x: x, y: oy + CGFloat(b.rows - 1) * cell))
                ctx.stroke(p, with: .color(lineColor), lineWidth: thick)
            } else {
                var p1 = Path()
                p1.move(to: CGPoint(x: x, y: oy))
                p1.addLine(to: CGPoint(x: x, y: oy + 4 * cell))
                ctx.stroke(p1, with: .color(lineColor), lineWidth: thick)
                var p2 = Path()
                p2.move(to: CGPoint(x: x, y: oy + 5 * cell))
                p2.addLine(to: CGPoint(x: x, y: oy + CGFloat(b.rows - 1) * cell))
                ctx.stroke(p2, with: .color(lineColor), lineWidth: thick)
            }
        }

        // 九宫斜线
        if b.rows >= 10, b.cols >= 9 {
            for pts in [
                (CGPoint(x: ox + 3 * cell, y: oy), CGPoint(x: ox + 5 * cell, y: oy + 2 * cell)),
                (CGPoint(x: ox + 5 * cell, y: oy), CGPoint(x: ox + 3 * cell, y: oy + 2 * cell)),
                (CGPoint(x: ox + 3 * cell, y: oy + 7 * cell), CGPoint(x: ox + 5 * cell, y: oy + 9 * cell)),
                (CGPoint(x: ox + 5 * cell, y: oy + 7 * cell), CGPoint(x: ox + 3 * cell, y: oy + 9 * cell)),
            ] {
                var p = Path()
                p.move(to: pts.0)
                p.addLine(to: pts.1)
                ctx.stroke(p, with: .color(lineColor), lineWidth: 1)
            }
        }

        // 炮/兵位角标
        if b.rows >= 10, b.cols >= 9 {
            let marks = [(2, 1), (2, 7), (7, 1), (7, 7),
                         (3, 0), (3, 2), (3, 4), (3, 6), (3, 8),
                         (6, 0), (6, 2), (6, 4), (6, 6), (6, 8)]
            for (r, c) in marks {
                drawXiangqiCornerMark(ctx, ox + CGFloat(c) * cell, oy + CGFloat(r) * cell, cell, lineColor)
            }
        }

        // 楚河汉界
        if b.rows >= 10 {
            let midY = oy + 4.5 * cell
            ctx.draw(Text("楚 河").font(.system(size: cell * 0.4, weight: .semibold))
                .foregroundColor(Color(red: 0.36, green: 0.25, blue: 0.22)),
                     at: CGPoint(x: ox + 2 * cell, y: midY), anchor: .center)
            ctx.draw(Text("汉 界").font(.system(size: cell * 0.4, weight: .semibold))
                .foregroundColor(Color(red: 0.36, green: 0.25, blue: 0.22)),
                     at: CGPoint(x: ox + 6 * cell, y: midY), anchor: .center)
        }

        // 棋子在交点
        let pieceR = cell * 0.38
        for r in 0..<b.rows {
            for c in 0..<b.cols {
                let tok = b.at(r, c)
                guard !tok.isEmpty else { continue }
                let cx = ox + CGFloat(c) * cell
                let cy = oy + CGFloat(r) * cell
                let red = tok.hasPrefix("r")
                let circle = Path(ellipseIn: CGRect(x: cx - pieceR, y: cy - pieceR, width: pieceR * 2, height: pieceR * 2))
                ctx.fill(circle, with: .color(red ? Color(red: 1, green: 0.97, blue: 0.88) : Color(white: 0.13)))
                ctx.stroke(circle, with: .color(red ? .red : .white), lineWidth: 1.5)
                ctx.draw(Text(String(tok.dropFirst())).font(.system(size: cell * 0.42, weight: .bold))
                    .foregroundColor(red ? .red : .white),
                         at: CGPoint(x: cx, y: cy), anchor: .center)
                if b.isLast(r, c) {
                    ctx.stroke(Path(ellipseIn: CGRect(x: cx - pieceR - 2, y: cy - pieceR - 2,
                                                      width: (pieceR + 2) * 2, height: (pieceR + 2) * 2)),
                               with: .color(.orange), lineWidth: 2.5)
                }
            }
        }
    }

    private func drawXiangqiCornerMark(_ ctx: GraphicsContext, _ cx: CGFloat, _ cy: CGFloat, _ cell: CGFloat, _ color: Color) {
        let s = cell * 0.14
        let g = cell * 0.08
        let segs: [(CGPoint, CGPoint)] = [
            (CGPoint(x: cx - g - s, y: cy - g), CGPoint(x: cx - g, y: cy - g)),
            (CGPoint(x: cx - g, y: cy - g - s), CGPoint(x: cx - g, y: cy - g)),
            (CGPoint(x: cx + g, y: cy - g), CGPoint(x: cx + g + s, y: cy - g)),
            (CGPoint(x: cx + g, y: cy - g - s), CGPoint(x: cx + g, y: cy - g)),
            (CGPoint(x: cx - g - s, y: cy + g), CGPoint(x: cx - g, y: cy + g)),
            (CGPoint(x: cx - g, y: cy + g), CGPoint(x: cx - g, y: cy + g + s)),
            (CGPoint(x: cx + g, y: cy + g), CGPoint(x: cx + g + s, y: cy + g)),
            (CGPoint(x: cx + g, y: cy + g), CGPoint(x: cx + g, y: cy + g + s)),
        ]
        for (a, b) in segs {
            var p = Path()
            p.move(to: a)
            p.addLine(to: b)
            ctx.stroke(p, with: .color(color), lineWidth: 1)
        }
    }

    private func drawStones(_ ctx: GraphicsContext, _ b: Board, _ ox: CGFloat, _ oy: CGFloat, _ cell: CGFloat) {
        let bg = CGRect(x: ox, y: oy, width: CGFloat(b.cols) * cell, height: CGFloat(b.rows) * cell)
        ctx.fill(Path(bg), with: .color(Color(red: 0.77, green: 0.65, blue: 0.45)))
        for r in 0..<b.rows {
            var p = Path()
            let y = oy + (CGFloat(r) + 0.5) * cell
            p.move(to: CGPoint(x: ox + 0.5 * cell, y: y))
            p.addLine(to: CGPoint(x: ox + (CGFloat(b.cols) - 0.5) * cell, y: y))
            ctx.stroke(p, with: .color(Color(red: 0.36, green: 0.25, blue: 0.22)), lineWidth: 1)
        }
        for c in 0..<b.cols {
            var p = Path()
            let x = ox + (CGFloat(c) + 0.5) * cell
            p.move(to: CGPoint(x: x, y: oy + 0.5 * cell))
            p.addLine(to: CGPoint(x: x, y: oy + (CGFloat(b.rows) - 0.5) * cell))
            ctx.stroke(p, with: .color(Color(red: 0.36, green: 0.25, blue: 0.22)), lineWidth: 1)
        }
        for r in 0..<b.rows {
            for c in 0..<b.cols {
                let tok = b.at(r, c)
                guard !tok.isEmpty else { continue }
                let cx = ox + (CGFloat(c) + 0.5) * cell
                let cy = oy + (CGFloat(r) + 0.5) * cell
                let circle = Path(ellipseIn: CGRect(x: cx - cell * 0.42, y: cy - cell * 0.42, width: cell * 0.84, height: cell * 0.84))
                ctx.fill(circle, with: .color(tok == "B" ? .black : .white))
                if tok == "W" { ctx.stroke(circle, with: .color(.gray), lineWidth: 1) }
                if b.isLast(r, c) {
                    ctx.stroke(Path(ellipseIn: CGRect(x: cx - cell * 0.18, y: cy - cell * 0.18, width: cell * 0.36, height: cell * 0.36)),
                               with: .color(.orange), lineWidth: 2)
                }
            }
        }
    }

    private func drawChess(_ ctx: GraphicsContext, _ b: Board, _ ox: CGFloat, _ oy: CGFloat, _ cell: CGFloat) {
        for r in 0..<b.rows {
            for c in 0..<b.cols {
                let rect = CGRect(x: ox + CGFloat(c) * cell, y: oy + CGFloat(r) * cell, width: cell, height: cell)
                let light = (r + c) % 2 == 0
                ctx.fill(Path(rect), with: .color(light ? Color(red: 0.94, green: 0.85, blue: 0.71) : Color(red: 0.71, green: 0.53, blue: 0.39)))
                let tok = b.at(r, c)
                if !tok.isEmpty {
                    ctx.draw(Text(tok).font(.system(size: cell * 0.7)), at: CGPoint(x: rect.midX, y: rect.midY), anchor: .center)
                }
                if b.isLast(r, c) {
                    ctx.stroke(Path(rect.insetBy(dx: 2, dy: 2)), with: .color(.orange), lineWidth: 2)
                }
            }
        }
    }

    private func drawJunqi(_ ctx: GraphicsContext, _ b: Board, _ ox: CGFloat, _ oy: CGFloat, _ cell: CGFloat) {
        let bg = CGRect(x: ox, y: oy, width: CGFloat(b.cols) * cell, height: CGFloat(b.rows) * cell)
        ctx.fill(Path(bg), with: .color(Color(red: 0.91, green: 0.96, blue: 0.91)))
        if b.rows == 12 {
            let mid = CGRect(x: ox, y: oy + 5 * cell, width: CGFloat(b.cols) * cell, height: 2 * cell)
            ctx.fill(Path(mid), with: .color(Color(red: 0.73, green: 0.87, blue: 0.98)))
        }
        for r in 0...b.rows {
            var p = Path()
            p.move(to: CGPoint(x: ox, y: oy + CGFloat(r) * cell))
            p.addLine(to: CGPoint(x: ox + CGFloat(b.cols) * cell, y: oy + CGFloat(r) * cell))
            ctx.stroke(p, with: .color(.brown.opacity(0.5)), lineWidth: 1)
        }
        for c in 0...b.cols {
            var p = Path()
            p.move(to: CGPoint(x: ox + CGFloat(c) * cell, y: oy))
            p.addLine(to: CGPoint(x: ox + CGFloat(c) * cell, y: oy + CGFloat(b.rows) * cell))
            ctx.stroke(p, with: .color(.brown.opacity(0.5)), lineWidth: 1)
        }
        for r in 0..<b.rows {
            for c in 0..<b.cols {
                let tok = b.at(r, c)
                guard !tok.isEmpty else { continue }
                let cx = ox + (CGFloat(c) + 0.5) * cell
                let cy = oy + (CGFloat(r) + 0.5) * cell
                let rect = CGRect(x: cx - cell * 0.38, y: cy - cell * 0.32, width: cell * 0.76, height: cell * 0.64)
                if tok == "?" {
                    ctx.fill(Path(roundedRect: rect, cornerRadius: 4), with: .color(.gray))
                    ctx.draw(Text("?").font(.system(size: cell * 0.4, weight: .bold)).foregroundColor(.white),
                             at: CGPoint(x: cx, y: cy), anchor: .center)
                } else {
                    let red = tok.hasPrefix("r")
                    ctx.fill(Path(roundedRect: rect, cornerRadius: 4), with: .color(red ? .red : .blue))
                    ctx.draw(Text(junqiLabel(String(tok.dropFirst()))).font(.system(size: cell * 0.35, weight: .bold)).foregroundColor(.white),
                             at: CGPoint(x: cx, y: cy), anchor: .center)
                }
            }
        }
    }

    private func junqiLabel(_ code: String) -> String {
        switch code {
        case "F": return "旗"; case "C": return "司"; case "A": return "军"
        case "D": return "师"; case "B": return "旅"; case "R": return "团"
        case "T": return "营"; case "N": return "连"; case "P": return "排"
        case "E": return "工"; case "M": return "雷"; case "O": return "炸"
        default: return code
        }
    }

    private func drawShip(_ ctx: GraphicsContext, _ b: Board, _ ox: CGFloat, _ oy: CGFloat, _ cell: CGFloat) {
        let bg = CGRect(x: ox, y: oy, width: CGFloat(b.cols) * cell, height: CGFloat(b.rows) * cell)
        ctx.fill(Path(bg), with: .color(Color(red: 0.01, green: 0.47, blue: 0.74)))
        for r in 0...b.rows {
            var p = Path()
            p.move(to: CGPoint(x: ox, y: oy + CGFloat(r) * cell))
            p.addLine(to: CGPoint(x: ox + CGFloat(b.cols) * cell, y: oy + CGFloat(r) * cell))
            ctx.stroke(p, with: .color(.white.opacity(0.3)), lineWidth: 1)
        }
        for c in 0...b.cols {
            var p = Path()
            p.move(to: CGPoint(x: ox + CGFloat(c) * cell, y: oy))
            p.addLine(to: CGPoint(x: ox + CGFloat(c) * cell, y: oy + CGFloat(b.rows) * cell))
            ctx.stroke(p, with: .color(.white.opacity(0.3)), lineWidth: 1)
        }
        for r in 0..<b.rows {
            for c in 0..<b.cols {
                let tok = b.at(r, c)
                let cx = ox + (CGFloat(c) + 0.5) * cell
                let cy = oy + (CGFloat(r) + 0.5) * cell
                switch tok {
                case "S":
                    let rect = CGRect(x: ox + CGFloat(c) * cell + 2, y: oy + CGFloat(r) * cell + 2, width: cell - 4, height: cell - 4)
                    ctx.fill(Path(rect), with: .color(.gray))
                case "X":
                    ctx.fill(Path(ellipseIn: CGRect(x: cx - cell * 0.3, y: cy - cell * 0.3, width: cell * 0.6, height: cell * 0.6)), with: .color(.red))
                case "o":
                    ctx.fill(Path(ellipseIn: CGRect(x: cx - cell * 0.18, y: cy - cell * 0.18, width: cell * 0.36, height: cell * 0.36)), with: .color(.cyan.opacity(0.7)))
                case "?":
                    ctx.draw(Text("·").foregroundColor(.cyan.opacity(0.6)), at: CGPoint(x: cx, y: cy), anchor: .center)
                default: break
                }
            }
        }
    }

    private func doushouTerrain(absRow: Int, absCol: Int) -> String {
        if absRow == 0 && absCol == 3 { return "黑穴" }
        if absRow == 8 && absCol == 3 { return "红穴" }
        if absRow == 0 && (absCol == 2 || absCol == 4) { return "黑陷" }
        if absRow == 1 && absCol == 3 { return "黑陷" }
        if absRow == 8 && (absCol == 2 || absCol == 4) { return "红陷" }
        if absRow == 7 && absCol == 3 { return "红陷" }
        if (3...5).contains(absRow) && [1, 2, 4, 5].contains(absCol) { return "河" }
        return ""
    }

    private func drawDoushou(_ ctx: GraphicsContext, _ b: Board, _ ox: CGFloat, _ oy: CGFloat, _ cell: CGFloat) {
        let terrainSet: Set<String> = ["河", "红穴", "黑穴", "红陷", "黑陷"]
        for r in 0..<b.rows {
            for c in 0..<b.cols {
                let absR = r < b.rowLabels.count ? b.rowLabels[r] - 1 : r
                let absC: Int = {
                    if c < b.colLabels.count { return (Int(b.colLabels[c]) ?? (c + 1)) - 1 }
                    return c
                }()
                let tok = b.at(r, c)
                let terrain = terrainSet.contains(tok) ? tok : doushouTerrain(absRow: absR, absCol: absC)
                let rect = CGRect(x: ox + CGFloat(c) * cell + 1, y: oy + CGFloat(r) * cell + 1,
                                  width: cell - 2, height: cell - 2)
                let bg: Color = {
                    switch terrain {
                    case "河": return Color(red: 0.51, green: 0.83, blue: 0.98)
                    case let t where t.hasSuffix("穴"): return Color(red: 1.0, green: 0.88, blue: 0.51)
                    case let t where t.hasSuffix("陷"): return Color(red: 0.94, green: 0.60, blue: 0.60)
                    default: return Color(red: 0.91, green: 0.79, blue: 0.57)
                    }
                }()
                ctx.fill(Path(roundedRect: rect, cornerRadius: 4), with: .color(bg))
                ctx.stroke(Path(roundedRect: rect, cornerRadius: 4), with: .color(Color(red: 0.55, green: 0.43, blue: 0.39)), lineWidth: 1)
                let cx = rect.midX
                let cy = rect.midY
                if tok.hasPrefix("r") || tok.hasPrefix("b") {
                    let red = tok.hasPrefix("r")
                    let circle = Path(ellipseIn: CGRect(x: cx - cell * 0.36, y: cy - cell * 0.36, width: cell * 0.72, height: cell * 0.72))
                    ctx.fill(circle, with: .color(red ? Color(red: 1, green: 0.97, blue: 0.88) : Color(white: 0.13)))
                    ctx.draw(Text(String(tok.dropFirst())).font(.system(size: cell * 0.4, weight: .bold)).foregroundColor(red ? .red : .white),
                             at: CGPoint(x: cx, y: cy), anchor: .center)
                } else if !terrain.isEmpty {
                    ctx.draw(Text(terrain).font(.system(size: cell * 0.26, weight: .semibold)).foregroundColor(Color(red: 0.36, green: 0.25, blue: 0.22)),
                             at: CGPoint(x: cx, y: cy), anchor: .center)
                }
                if b.isLast(r, c) {
                    ctx.stroke(Path(ellipseIn: CGRect(x: cx - cell * 0.4, y: cy - cell * 0.4, width: cell * 0.8, height: cell * 0.8)),
                               with: .color(.orange), lineWidth: 2)
                }
            }
        }
    }

    private func drawGrid(_ ctx: GraphicsContext, _ b: Board, _ ox: CGFloat, _ oy: CGFloat, _ cell: CGFloat) {
        let bg = CGRect(x: ox, y: oy, width: CGFloat(b.cols) * cell, height: CGFloat(b.rows) * cell)
        ctx.fill(Path(bg), with: .color(Color(red: 0.87, green: 0.72, blue: 0.53)))
        for r in 0...b.rows {
            var p = Path()
            p.move(to: CGPoint(x: ox, y: oy + CGFloat(r) * cell))
            p.addLine(to: CGPoint(x: ox + CGFloat(b.cols) * cell, y: oy + CGFloat(r) * cell))
            ctx.stroke(p, with: .color(.brown), lineWidth: 1)
        }
        for c in 0...b.cols {
            var p = Path()
            p.move(to: CGPoint(x: ox + CGFloat(c) * cell, y: oy))
            p.addLine(to: CGPoint(x: ox + CGFloat(c) * cell, y: oy + CGFloat(b.rows) * cell))
            ctx.stroke(p, with: .color(.brown), lineWidth: 1)
        }
        for r in 0..<b.rows {
            for c in 0..<b.cols {
                let tok = b.at(r, c)
                guard !tok.isEmpty else { continue }
                let cx = ox + (CGFloat(c) + 0.5) * cell
                let cy = oy + (CGFloat(r) + 0.5) * cell
                if tok == "?" {
                    ctx.fill(Path(ellipseIn: CGRect(x: cx - cell * 0.38, y: cy - cell * 0.38, width: cell * 0.76, height: cell * 0.76)), with: .color(.gray))
                    ctx.draw(Text("?").bold().foregroundColor(.white), at: CGPoint(x: cx, y: cy), anchor: .center)
                } else if tok.hasPrefix("r") || tok.hasPrefix("b") {
                    let red = tok.hasPrefix("r")
                    ctx.fill(Path(ellipseIn: CGRect(x: cx - cell * 0.4, y: cy - cell * 0.4, width: cell * 0.8, height: cell * 0.8)),
                             with: .color(red ? Color(red: 1, green: 0.97, blue: 0.88) : Color(white: 0.13)))
                    ctx.draw(Text(String(tok.dropFirst())).font(.system(size: cell * 0.4, weight: .bold)).foregroundColor(red ? .red : .white),
                             at: CGPoint(x: cx, y: cy), anchor: .center)
                } else {
                    ctx.draw(Text(tok).font(.system(size: cell * 0.4)), at: CGPoint(x: cx, y: cy), anchor: .center)
                }
            }
        }
    }
}
