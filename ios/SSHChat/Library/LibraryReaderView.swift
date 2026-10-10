import SwiftUI
import Combine

@MainActor
final class LibraryReaderModel: ObservableObject {
    enum Mode { case catalog, reading }

    struct Theme: Identifiable {
        let id: Int
        let name: String
        let bg: Color
        let text: Color
        let sub: Color
        let chrome: Color
        let accent: Color
        let light: Bool
    }

    static let themes: [Theme] = [
        Theme(id: 0, name: "日间", bg: Color(red: 0.98, green: 0.976, blue: 0.965), text: Color(white: 0.17), sub: Color(white: 0.54), chrome: .white, accent: Color(red: 0.106, green: 0.369, blue: 0.125), light: true),
        Theme(id: 1, name: "护眼", bg: Color(red: 0.957, green: 0.925, blue: 0.847), text: Color(red: 0.357, green: 0.275, blue: 0.212), sub: Color(red: 0.612, green: 0.545, blue: 0.459), chrome: Color(red: 0.937, green: 0.894, blue: 0.796), accent: Color(red: 0.553, green: 0.333, blue: 0.141), light: true),
        Theme(id: 2, name: "豆沙绿", bg: Color(red: 0.8, green: 0.91, blue: 0.812), text: Color(red: 0.18, green: 0.23, blue: 0.184), sub: Color(red: 0.373, green: 0.478, blue: 0.38), chrome: Color(red: 0.745, green: 0.867, blue: 0.757), accent: Color(red: 0.18, green: 0.49, blue: 0.196), light: true),
        Theme(id: 3, name: "夜间", bg: Color(white: 0.078), text: Color(white: 0.62), sub: Color(white: 0.37), chrome: Color(white: 0.12), accent: Color(red: 0.506, green: 0.78, blue: 0.518), light: false),
    ]

    @Published var visible = false
    @Published var mode: Mode = .catalog
    @Published var books: [LibraryBook] = []
    @Published var catalogNotes: [String] = []
    @Published var query = ""
    @Published var page: LibraryPage?
    @Published var currentBook: LibraryBook?
    @Published var loading = false
    @Published var loadingText = ""
    @Published var chromeVisible = false
    @Published var settingsVisible = false
    @Published var toast: String?
    @Published var searchHits: [LibrarySearchHit] = []
    @Published var searchQuery = ""
    @Published var showSearchSheet = false
    @Published var showJumpSheet = false
    @Published var jumpDraft = ""
    @Published var inBookSearchDraft = ""
    @Published var showInBookSearch = false

    @Published var fontSize: CGFloat
    @Published var lineMult: CGFloat
    @Published var themeIdx: Int
    @Published var serif: Bool

    var onSend: ((String) -> Bool)?

    private var reopenTried = false
    private var landAtEnd = false
    private var requestPending = false
    private var flushTask: Task<Void, Never>?
    private let parser = LibraryParser()
    private let defaults = UserDefaults.standard

    var theme: Theme { Self.themes[themeIdx.clamped(to: 0...Self.themes.count - 1)] }

    var filteredBooks: [LibraryBook] {
        let terms = query.trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased()
            .split(whereSeparator: \.isWhitespace)
            .map(String.init)
        guard !terms.isEmpty else { return books }
        return books.filter { b in
            let hay = "\(b.name) \(b.format) \(b.origin ?? "")".lowercased()
            return terms.allSatisfy { hay.contains($0) }
        }
    }

    var continueReading: [LibraryBook] {
        query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            ? books.filter { $0.bookmarkPage != nil }
            : []
    }

    init() {
        fontSize = CGFloat(defaults.object(forKey: "library_font_sp") as? Double ?? 19)
        lineMult = CGFloat(defaults.object(forKey: "library_line_mult") as? Double ?? 1.75)
        themeIdx = defaults.object(forKey: "library_theme") as? Int ?? 0
        serif = defaults.object(forKey: "library_serif") as? Bool ?? true
    }

    var isReading: Bool { visible && mode == .reading }

    func openCatalog() {
        visible = true
        mode = .catalog
        chromeVisible = false
        settingsVisible = false
        requestCatalog()
    }

    func hide() {
        visible = false
        loading = false
        requestPending = false
        flushTask?.cancel()
    }

    func reset() {
        hide()
        parser.reset()
        books = []
        catalogNotes = []
        page = nil
        currentBook = nil
        reopenTried = false
    }

    @discardableResult
    func handleBack() -> Bool {
        guard visible else { return false }
        if mode == .reading, settingsVisible {
            settingsVisible = false
            return true
        }
        if mode == .reading {
            leaveBook()
            return true
        }
        hide()
        return true
    }

    /// Feed a board/system star body. Returns true when the open reader consumed it.
    func feedLine(_ text: String) -> Bool {
        let r = parser.feed(text)
        if !r.events.isEmpty { handle(r.events) }
        flushTask?.cancel()
        if r.libraryLine {
            flushTask = Task { [weak self] in
                try? await Task.sleep(nanoseconds: 350_000_000)
                guard !Task.isCancelled, let self else { return }
                let events = self.parser.flush()
                if !events.isEmpty { self.handle(events) }
            }
        }
        return r.libraryLine && visible
    }

    func handle(_ events: [LibraryEvent]) {
        for ev in events { handleEvent(ev) }
    }

    private func handleEvent(_ ev: LibraryEvent) {
        switch ev {
        case .catalogStart:
            if mode != .reading { mode = .catalog }
            loading = true
            loadingText = "正在获取书目…"
        case let .catalog(list, notes):
            books = list
            catalogNotes = notes
            if mode == .catalog { setLoading(false) }
        case .pageStart:
            if mode != .reading { mode = .reading }
        case let .page(p):
            showPage(p)
        case let .searchResults(title, query, hits):
            setLoading(false)
            if hits.isEmpty {
                toast = "《\(title)》中没有找到「\(query)」"
            } else if hits.count == 1 {
                break
            } else {
                searchHits = hits
                searchQuery = query
                showSearchSheet = true
            }
        case let .notice(t):
            if visible { toast = t.trimmingCharacters(in: CharacterSet(charactersIn: "。")) }
        case let .loading(t):
            if visible { setLoading(true, t) }
        case let .error(t):
            if visible {
                setLoading(false)
                toast = t
            }
        case .closed:
            break
        case .needsReopen:
            if visible, let book = currentBook, !reopenTried {
                reopenTried = true
                send("/library open \(book.openToken)")
            } else if visible {
                setLoading(false)
                toast = "请先从书目中打开一本书"
                if mode == .reading { leaveBook() }
            }
        }
    }

    private func showPage(_ p: LibraryPage) {
        let prev = page
        let same = prev?.title == p.title && prev?.page == p.page
        page = p
        reopenTried = false
        setLoading(false)
        mode = .reading
        if let book = currentBook {
            books = books.map {
                $0.index == book.index
                    ? LibraryBook(index: $0.index, format: $0.format, name: $0.name, size: $0.size, origin: $0.origin, bookmarkPage: p.page)
                    : $0
            }
        }
        if !same { landAtEnd = false }
        _ = same
    }

    func requestCatalog() {
        setLoading(true, "正在获取书目…")
        send("/library")
    }

    func openBook(_ book: LibraryBook) {
        currentBook = book
        reopenTried = false
        page = nil
        landAtEnd = false
        setLoading(true, "正在打开《\(book.displayTitle)》…")
        send("/library open \(book.openToken)")
    }

    func leaveBook() {
        settingsVisible = false
        chromeVisible = false
        send("/library close")
        page = nil
        mode = .catalog
        requestCatalog()
    }

    func turnPage(_ delta: Int, landAtEnd: Bool = false) {
        guard let p = page, !requestPending else { return }
        let target = p.page + delta
        if target < 1 { toast = "已是第一页"; return }
        if target > p.total { toast = "已是最后一页"; return }
        self.landAtEnd = landAtEnd
        setLoading(true, delta > 0 ? "下一页…" : "上一页…")
        send(delta > 0 ? "/library next" : "/library prev")
    }

    func jumpTo(_ target: Int) {
        guard let p = page else { return }
        let t = min(max(target, 1), p.total)
        guard t != p.page else { return }
        landAtEnd = false
        setLoading(true, "跳转到第 \(t) 页…")
        send("/library page \(t)")
    }

    func searchInBook(_ q: String) {
        let query = q.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { return }
        setLoading(true, "搜索「\(query)」…")
        send("/library search \(query)")
    }

    func stepForward() {
        guard isReading else { return }
        turnPage(+1)
    }

    func stepBackward() {
        guard isReading else { return }
        turnPage(-1, landAtEnd: true)
    }

    func bumpFont(_ d: CGFloat) {
        fontSize = min(32, max(13, fontSize + d))
        defaults.set(Double(fontSize), forKey: "library_font_sp")
    }

    func bumpLine(_ d: CGFloat) {
        lineMult = (min(2.5, max(1.2, lineMult + d)) * 100).rounded() / 100
        defaults.set(Double(lineMult), forKey: "library_line_mult")
    }

    func setTheme(_ i: Int) {
        themeIdx = i.clamped(to: 0...Self.themes.count - 1)
        defaults.set(themeIdx, forKey: "library_theme")
    }

    func setSerif(_ on: Bool) {
        serif = on
        defaults.set(on, forKey: "library_serif")
    }

    private func send(_ cmd: String) {
        if onSend?(cmd) != true {
            setLoading(false)
        }
    }

    private func setLoading(_ on: Bool, _ text: String = "") {
        requestPending = on
        loading = on
        loadingText = text.isEmpty ? "加载中…" : text
    }
}

private extension Int {
    func clamped(to range: ClosedRange<Int>) -> Int {
        Swift.min(Swift.max(self, range.lowerBound), range.upperBound)
    }
}

// MARK: - Root cover

struct LibraryReaderView: View {
    @ObservedObject var model: LibraryReaderModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        ZStack {
            model.theme.bg.ignoresSafeArea()
            if model.mode == .catalog {
                catalogPane
            } else {
                readingPane
            }
            if model.loading {
                loadingPill
            }
        }
        .preferredColorScheme(model.theme.light ? .light : .dark)
        .statusBarHidden(model.mode == .reading && !model.chromeVisible)
        .alert("跳转到页码", isPresented: $model.showJumpSheet) {
            TextField("页码", text: $model.jumpDraft)
                .keyboardType(.numberPad)
            Button("跳转") {
                if let n = Int(model.jumpDraft.trimmingCharacters(in: .whitespaces)) {
                    model.jumpTo(n)
                }
            }
            Button("取消", role: .cancel) {}
        }
        .alert("书内搜索", isPresented: $model.showInBookSearch) {
            TextField("关键词", text: $model.inBookSearchDraft)
            Button("搜索") { model.searchInBook(model.inBookSearchDraft) }
            Button("取消", role: .cancel) {}
        }
        .confirmationDialog(
            "「\(model.searchQuery)」共 \(model.searchHits.count) 处",
            isPresented: $model.showSearchSheet,
            titleVisibility: .visible
        ) {
            ForEach(model.searchHits) { hit in
                Button("第 \(hit.page) 页　\(hit.snippet)") {
                    model.jumpTo(hit.page)
                }
            }
            Button("关闭", role: .cancel) {}
        }
        .overlay(alignment: .bottom) {
            if let toast = model.toast {
                Text(toast)
                    .font(.footnote)
                    .padding(10)
                    .background(.black.opacity(0.8))
                    .foregroundStyle(.white)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                    .padding(.bottom, 28)
                    .onAppear {
                        DispatchQueue.main.asyncAfter(deadline: .now() + 2) {
                            if model.toast == toast { model.toast = nil }
                        }
                    }
            }
        }
        .onAppear {
            UIApplication.shared.isIdleTimerDisabled = model.mode == .reading
        }
        .onChange(of: model.mode) { _, m in
            UIApplication.shared.isIdleTimerDisabled = m == .reading && model.visible
        }
        .onDisappear {
            UIApplication.shared.isIdleTimerDisabled = false
        }
    }

    // MARK: Catalog

    private var catalogPane: some View {
        VStack(spacing: 0) {
            HStack {
                Button("‹ 聊天") {
                    model.hide()
                    dismiss()
                }
                .foregroundStyle(model.theme.accent)
                Spacer()
                Text("图书馆")
                    .font(.system(size: 18, weight: .bold))
                    .foregroundStyle(model.theme.text)
                Spacer()
                Button("刷新") { model.requestCatalog() }
                    .foregroundStyle(model.theme.accent)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 12)
            .background(model.theme.chrome)

            TextField("搜索书名 / 格式", text: $model.query)
                .textFieldStyle(.plain)
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .background(model.theme.chrome)
                .clipShape(Capsule())
                .overlay(Capsule().stroke(model.theme.sub.opacity(0.35), lineWidth: 1))
                .padding(.horizontal, 14)
                .padding(.vertical, 8)
                .foregroundStyle(model.theme.text)

            Text(catalogSummary)
                .font(.system(size: 12))
                .foregroundStyle(model.theme.sub)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 16)
                .padding(.bottom, 4)

            ScrollView {
                LazyVStack(alignment: .leading, spacing: 8) {
                    if !model.continueReading.isEmpty {
                        sectionLabel("继续阅读")
                        ForEach(model.continueReading) { book in
                            bookRow(book, highlight: true)
                        }
                        sectionLabel("全部图书")
                    }
                    ForEach(model.filteredBooks) { book in
                        bookRow(book, highlight: false)
                    }
                    if model.books.isEmpty == false && model.filteredBooks.isEmpty {
                        Text("没有匹配的图书")
                            .foregroundStyle(model.theme.sub)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 32)
                    }
                }
                .padding(.horizontal, 12)
                .padding(.bottom, 24)
            }
        }
        .background(model.theme.bg.opacity(0.97))
    }

    private var catalogSummary: String {
        if model.books.isEmpty, !model.catalogNotes.isEmpty {
            return model.catalogNotes.joined(separator: "\n")
        }
        if model.books.isEmpty { return "正在获取书目…" }
        let terms = model.query.trimmingCharacters(in: .whitespacesAndNewlines)
        if !terms.isEmpty {
            return "找到 \(model.filteredBooks.count) / \(model.books.count) 本"
        }
        return "共 \(model.books.count) 本 · 点击书名开始阅读，书签自动保存"
    }

    private func sectionLabel(_ s: String) -> some View {
        Text(s)
            .font(.system(size: 13, weight: .bold))
            .foregroundStyle(model.theme.sub)
            .padding(.top, 10)
            .padding(.horizontal, 6)
    }

    private func bookRow(_ book: LibraryBook, highlight: Bool) -> some View {
        Button {
            model.openBook(book)
        } label: {
            HStack(spacing: 12) {
                Text(String(book.format.prefix(4)))
                    .font(.system(size: 11, weight: .bold))
                    .foregroundStyle(.white)
                    .frame(width: 40, height: 54)
                    .background(formatColor(book.format))
                    .clipShape(RoundedRectangle(cornerRadius: 4))
                VStack(alignment: .leading, spacing: 4) {
                    Text(book.displayTitle)
                        .font(.system(size: 16))
                        .foregroundStyle(model.theme.text)
                        .lineLimit(2)
                        .multilineTextAlignment(.leading)
                    HStack(spacing: 0) {
                        Text("#\(book.index) · \(book.size)")
                        if let o = book.origin, !o.isEmpty {
                            Text(" · @\(o)")
                        }
                    }
                    .font(.system(size: 12))
                    .foregroundStyle(model.theme.sub)
                    if let p = book.bookmarkPage {
                        Text("读到第 \(p) 页")
                            .font(.system(size: 12))
                            .foregroundStyle(model.theme.accent)
                    }
                }
                Spacer(minLength: 0)
                Text("›")
                    .font(.system(size: 22))
                    .foregroundStyle(model.theme.sub)
            }
            .padding(12)
            .background(
                RoundedRectangle(cornerRadius: 10)
                    .fill(highlight ? model.theme.accent.opacity(0.08) : model.theme.chrome)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 10)
                    .stroke(model.theme.text.opacity(0.1), lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
    }

    private func formatColor(_ fmt: String) -> Color {
        switch fmt.uppercased() {
        case "EPUB": return Color(red: 0.361, green: 0.42, blue: 0.753)
        case "PDF": return Color(red: 0.776, green: 0.157, blue: 0.157)
        case "MD": return Color(red: 0, green: 0.537, blue: 0.482)
        default: return Color(red: 0.427, green: 0.298, blue: 0.255)
        }
    }

    // MARK: Reading

    private var readingPane: some View {
        ZStack(alignment: .bottom) {
            VStack(spacing: 0) {
                GeometryReader { geo in
                    ScrollViewReader { proxy in
                        ScrollView {
                            VStack(alignment: .leading, spacing: 0) {
                                if let p = model.page {
                                    Text("《\(p.title)》")
                                        .font(.system(size: 12))
                                        .foregroundStyle(model.theme.sub)
                                        .padding(.bottom, 14)
                                    bodyText(p)
                                        .id("body")
                                    Text(p.page >= p.total ? "— 全书完 —" : "— 第 \(p.page) / \(p.total) 页 —")
                                        .font(.system(size: 12))
                                        .foregroundStyle(model.theme.sub)
                                        .frame(maxWidth: .infinity)
                                        .padding(.top, 28)
                                    if p.page < p.total {
                                        Button {
                                            model.turnPage(+1)
                                        } label: {
                                            Text("继续阅读第 \(p.page + 1) 页  ›")
                                                .font(.system(size: 15))
                                                .foregroundStyle(model.theme.accent)
                                                .frame(maxWidth: .infinity)
                                                .padding(.vertical, 12)
                                                .background(model.theme.text.opacity(0.06))
                                                .clipShape(Capsule())
                                        }
                                        .buttonStyle(.plain)
                                        .padding(.top, 10)
                                        .padding(.horizontal, 24)
                                    }
                                }
                            }
                            .padding(.horizontal, 22)
                            .padding(.top, 18)
                            .padding(.bottom, 40)
                            .frame(minHeight: geo.size.height - 28)
                            .contentShape(Rectangle())
                        }
                        .scrollIndicators(.hidden)
                        .simultaneousGesture(
                            SpatialTapGesture()
                                .onEnded { event in
                                    let x = event.location.x
                                    let w = geo.size.width
                                    if model.chromeVisible {
                                        model.chromeVisible = false
                                        model.settingsVisible = false
                                    } else if x < w * 0.3 {
                                        model.stepBackward()
                                    } else if x > w * 0.7 {
                                        model.stepForward()
                                    } else {
                                        model.chromeVisible = true
                                    }
                                }
                        )
                        .simultaneousGesture(
                            DragGesture(minimumDistance: 40)
                                .onEnded { value in
                                    let dx = value.translation.width
                                    let dy = value.translation.height
                                    if abs(dx) > 72, abs(dx) > abs(dy) * 1.6 {
                                        if dx < 0 { model.turnPage(+1) } else { model.turnPage(-1) }
                                    }
                                }
                        )
                        .onChange(of: model.page?.page) { _, _ in
                            proxy.scrollTo("body", anchor: .top)
                        }
                    }
                }

                HStack {
                    Text(model.page?.title ?? model.currentBook?.displayTitle ?? "")
                        .font(.system(size: 11))
                        .foregroundStyle(model.theme.sub)
                        .lineLimit(1)
                    Spacer()
                    if let p = model.page {
                        let pct = p.total <= 1 ? 100 : Int(Double(p.page - 1) * 100 / Double(p.total - 1))
                        Text("\(p.page)/\(p.total) · \(pct)%")
                            .font(.system(size: 11))
                            .foregroundStyle(model.theme.sub)
                    }
                }
                .padding(.horizontal, 22)
                .padding(.vertical, 6)
            }

            if model.chromeVisible {
                VStack(spacing: 0) {
                    topBar
                    Spacer()
                    bottomBar
                }
            }
        }
    }

    private func bodyText(_ p: LibraryPage) -> some View {
        let paragraphs = p.paragraphs.isEmpty ? ["（空白页）"] : p.paragraphs
        return VStack(alignment: .leading, spacing: model.fontSize * (model.lineMult - 1) * 0.55) {
            ForEach(Array(paragraphs.enumerated()), id: \.offset) { _, para in
                Text(indent(para))
                    .font(model.serif
                          ? .system(size: model.fontSize, design: .serif)
                          : .system(size: model.fontSize))
                    .foregroundStyle(model.theme.text)
                    .lineSpacing(model.fontSize * (model.lineMult - 1))
                    .tracking(0.3)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .multilineTextAlignment(.leading)
            }
        }
    }

    private func indent(_ para: String) -> String {
        guard let first = para.first else { return para }
        let open: Set<Character> = ["「", "『", "“", "‘", "（", "《", "〈", "【", "〔", "—", "…"]
        if first.unicodeScalars.first!.value >= 0x2E80, !open.contains(first) {
            return "\u{3000}\u{3000}" + para
        }
        return para
    }

    private var topBar: some View {
        HStack {
            Button("‹ 书目") { model.leaveBook() }
                .foregroundStyle(model.theme.accent)
            Spacer()
            Text(model.page?.title ?? "")
                .font(.system(size: 15, weight: .bold))
                .foregroundStyle(model.theme.text)
                .lineLimit(1)
            Spacer()
            Button("搜索") {
                model.inBookSearchDraft = ""
                model.showInBookSearch = true
            }
            .foregroundStyle(model.theme.accent)
            Button("Aa") {
                model.settingsVisible.toggle()
            }
            .font(.system(size: 16, weight: .bold))
            .foregroundStyle(model.theme.accent)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .background(model.theme.chrome.opacity(0.97))
    }

    private var bottomBar: some View {
        VStack(spacing: 8) {
            if model.settingsVisible {
                settingsPanel
            }
            if let p = model.page {
                let pct = p.total <= 1 ? 100 : Int(Double(p.page - 1) * 100 / Double(p.total - 1))
                Button {
                    model.jumpDraft = "\(p.page)"
                    model.showJumpSheet = true
                } label: {
                    Text("第 \(p.page) / \(p.total) 页 · \(pct)%　（点此输入页码）")
                        .font(.system(size: 12))
                        .foregroundStyle(model.theme.sub)
                }
                .buttonStyle(.plain)

                HStack {
                    Button("上一页") { model.turnPage(-1) }
                        .disabled(p.page <= 1)
                        .opacity(p.page <= 1 ? 0.35 : 1)
                        .foregroundStyle(model.theme.accent)
                    Slider(
                        value: Binding(
                            get: { Double(p.page) },
                            set: { model.jumpTo(Int($0.rounded())) }
                        ),
                        in: 1...Double(max(p.total, 1)),
                        step: 1
                    )
                    .tint(model.theme.accent)
                    Button("下一页") { model.turnPage(+1) }
                        .disabled(p.page >= p.total)
                        .opacity(p.page >= p.total ? 0.35 : 1)
                        .foregroundStyle(model.theme.accent)
                }
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .background(model.theme.chrome.opacity(0.97))
    }

    private var settingsPanel: some View {
        VStack(spacing: 10) {
            HStack {
                Text("字号").foregroundStyle(model.theme.text)
                Button("A−") { model.bumpFont(-1) }
                Spacer()
                Text("\(Int(model.fontSize))").foregroundStyle(model.theme.text)
                Spacer()
                Button("A+") { model.bumpFont(1) }
            }
            HStack {
                Text("行距").foregroundStyle(model.theme.text)
                Button("−") { model.bumpLine(-0.15) }
                Spacer()
                Text(String(format: "%.2f", model.lineMult)).foregroundStyle(model.theme.text)
                Spacer()
                Button("+") { model.bumpLine(0.15) }
            }
            HStack(spacing: 8) {
                ForEach(LibraryReaderModel.themes) { th in
                    Button {
                        model.setTheme(th.id)
                    } label: {
                        Text(th.name)
                            .font(.system(size: 13))
                            .foregroundStyle(th.text)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 10)
                            .background(th.bg)
                            .clipShape(Capsule())
                            .overlay(
                                Capsule().stroke(
                                    th.id == model.themeIdx ? model.theme.accent : th.text.opacity(0.25),
                                    lineWidth: th.id == model.themeIdx ? 2 : 1
                                )
                            )
                    }
                    .buttonStyle(.plain)
                }
            }
            HStack {
                Text("字体").foregroundStyle(model.theme.text)
                Button {
                    model.setSerif(true)
                } label: {
                    Text("宋体")
                        .font(.system(size: 14, design: .serif))
                        .padding(.horizontal, 16)
                        .padding(.vertical, 8)
                        .background(model.theme.chrome)
                        .clipShape(Capsule())
                        .overlay(Capsule().stroke(model.serif ? model.theme.accent : model.theme.sub.opacity(0.4), lineWidth: 1))
                }
                .buttonStyle(.plain)
                .foregroundStyle(model.theme.text)
                Button {
                    model.setSerif(false)
                } label: {
                    Text("黑体")
                        .font(.system(size: 14))
                        .padding(.horizontal, 16)
                        .padding(.vertical, 8)
                        .background(model.theme.chrome)
                        .clipShape(Capsule())
                        .overlay(Capsule().stroke(!model.serif ? model.theme.accent : model.theme.sub.opacity(0.4), lineWidth: 1))
                }
                .buttonStyle(.plain)
                .foregroundStyle(model.theme.text)
                Spacer()
            }
        }
        .padding(.bottom, 4)
    }

    private var loadingPill: some View {
        HStack(spacing: 8) {
            ProgressView().tint(.white)
            Text(model.loadingText)
                .font(.system(size: 13))
                .foregroundStyle(.white)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
        .background(Capsule().fill(Color.black.opacity(0.8)))
        .padding(.top, 64)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
    }
}
