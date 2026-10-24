import SwiftUI
import Combine
import UIKit

@MainActor
final class NewsPanelModel: ObservableObject {
    @Published var visible = false
    @Published var items: [NewsItem] = []
    @Published var filter = ""
    @Published var loading = false
    @Published var summary = "点一行打开原文网页"
    @Published var toast: String?
    @Published var heard = 0
    @Published var samples: [String] = []

    var onSend: ((String) -> Bool)?

    private let parser = NewsParser()
    private var requestPending = false
    private var flushTask: Task<Void, Never>?
    private var timeoutTask: Task<Void, Never>?

    /// Keep feeding `/news` rows even if the overlay flag glitches.
    var wantsFeed: Bool { visible || requestPending }

    var shown: [NewsItem] {
        filter.isEmpty ? items : items.filter { $0.categoryToken == filter }
    }

    func open() {
        visible = true
        filter = ""
        parser.reset()
        items = []
        heard = 0
        samples = []
        requestNews()
    }

    func hide() {
        visible = false
        loading = false
        requestPending = false
        flushTask?.cancel()
        timeoutTask?.cancel()
    }

    func reset() {
        hide()
        parser.reset()
        items = []
        filter = ""
        summary = "点一行打开原文网页"
    }

    func requestNews() {
        guard onSend?("/news") == true else {
            summary = "无法发送 /news（未连接？）"
            return
        }
        parser.reset()
        items = []
        heard = 0
        samples = []
        filter = ""
        requestPending = true
        loading = true
        summary = "正在获取新闻…"
        timeoutTask?.cancel()
        timeoutTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 45_000_000_000)
            guard !Task.isCancelled, let self, self.requestPending else { return }
            self.loading = false
            self.requestPending = false
            if self.items.isEmpty {
                self.toast = self.heard == 0
                    ? "没收到服务器返回，请在聊天里发 /news 试一次"
                    : "收到 \(self.heard) 行但没解析到标题"
                self.refreshSummary()
            }
        }
    }

    func feedLine(_ text: String) -> Bool {
        heard += 1
        let r = parser.feed(text)
        samples = parser.snapshotSamples()
        if !r.events.isEmpty { handle(r.events) }
        flushTask?.cancel()
        flushTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 400_000_000)
            guard !Task.isCancelled, let self else { return }
            let events = self.parser.flush()
            self.samples = self.parser.snapshotSamples()
            if !events.isEmpty {
                self.handle(events)
            } else if !self.loading {
                self.refreshSummary()
            }
        }
        return r.newsLine && (visible || requestPending)
    }

    func handle(_ events: [NewsEvent]) {
        for ev in events {
            switch ev {
            case .items(let list):
                items = list
                loading = false
                refreshSummary()
            case .notice(let text):
                if items.isEmpty {
                    if text.hasPrefix("暂时没有") {
                        summary = text
                        loading = false
                    }
                }
            }
        }
    }

    func originalURL(_ item: NewsItem) -> URL? {
        let raw = item.link.trimmingCharacters(in: .whitespacesAndNewlines)
        guard raw.lowercased().hasPrefix("http://") || raw.lowercased().hasPrefix("https://") else {
            return nil
        }
        if let url = URL(string: raw) { return url }
        let allowed = CharacterSet.urlFragmentAllowed
        guard let encoded = raw.addingPercentEncoding(withAllowedCharacters: allowed) else { return nil }
        return URL(string: encoded)
    }

    func openOriginal(_ item: NewsItem) {
        guard let url = originalURL(item) else {
            toast = "这条没有原文链接"
            return
        }
        UIApplication.shared.open(url)
    }

    func refreshSummary() {
        let n = shown.count
        if n == 0 {
            if loading {
                summary = heard == 0 ? "正在获取新闻…" : "正在获取新闻… 已收 \(heard) 行"
            } else if heard == 0 {
                summary = "没收到返回，点刷新或在聊天发 /news"
            } else {
                summary = "收到 \(heard) 行，没有解析到标题"
            }
        } else {
            summary = "共 \(n) 条 · 点一行打开原文网页"
        }
    }
}

struct NewsPanelView: View {
    @ObservedObject var model: NewsPanelModel
    @Environment(\.dismiss) private var dismiss
    @Environment(\.openURL) private var openURL

    private let cats: [(String, String)] = [
        ("全部", ""), ("中文", "中文"), ("国际", "国际"), ("科技", "科技"),
    ]
    private let accent = Color(red: 0.106, green: 0.369, blue: 0.125)
    private let textColor = Color(white: 0.17)
    private let sub = Color(white: 0.54)

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Button("‹ 聊天") {
                    model.hide()
                    dismiss()
                }
                .accessibilityLabel("返回聊天")
                .foregroundStyle(accent)
                Spacer()
                Text("新闻")
                    .font(.system(size: 18, weight: .bold))
                    .foregroundStyle(textColor)
                Spacer()
                Button("刷新") { model.requestNews() }
                    .foregroundStyle(accent)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 12)
            .background(Color.white)

            HStack(spacing: 8) {
                ForEach(cats, id: \.1) { label, token in
                    let on = model.filter == token
                    Button(label) {
                        model.filter = token
                        model.refreshSummary()
                    }
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(on ? .white : accent)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 8)
                    .background(on ? accent : Color.white)
                    .clipShape(Capsule())
                    .overlay(Capsule().stroke(accent, lineWidth: 1))
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)

            HStack {
                Text(model.summary)
                    .font(.system(size: 12))
                    .foregroundStyle(sub)
                Spacer()
                if model.loading {
                    ProgressView()
                        .scaleEffect(0.8)
                }
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 8)

            if model.shown.isEmpty {
                VStack(spacing: 12) {
                    Text(model.loading
                         ? (model.heard == 0 ? "正在获取新闻…" : "正在解析… 已收 \(model.heard) 行")
                         : model.summary)
                        .font(.system(size: 15))
                        .foregroundStyle(sub)
                        .multilineTextAlignment(.center)
                    if !model.samples.isEmpty {
                        Text(model.samples.joined(separator: "\n"))
                            .font(.system(size: 11, design: .monospaced))
                            .foregroundStyle(sub)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(10)
                            .background(Color.white)
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                    }
                }
                .padding(.horizontal, 20)
                .padding(.top, 24)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
            } else {
                List {
                    ForEach(Array(groupedSections), id: \.label) { section in
                        Section(section.label) {
                            ForEach(section.rows) { item in
                                newsRow(item)
                                    .listRowInsets(EdgeInsets(top: 6, leading: 12, bottom: 6, trailing: 12))
                                    .listRowSeparator(.hidden)
                                    .listRowBackground(Color.clear)
                            }
                        }
                    }
                }
                .listStyle(.plain)
                .scrollContentBackground(.hidden)
            }
        }
        .background(Color(red: 0.98, green: 0.976, blue: 0.965).ignoresSafeArea())
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
    }

    private var groupedSections: [(label: String, rows: [NewsItem])] {
        var seen: [String] = []
        var map: [String: [NewsItem]] = [:]
        for item in model.shown {
            let label = item.categoryLabel.isEmpty ? "新闻" : item.categoryLabel
            if map[label] == nil { seen.append(label) }
            map[label, default: []].append(item)
        }
        return seen.map { (label: $0, rows: map[$0] ?? []) }
    }

    private func newsRow(_ item: NewsItem) -> some View {
        Button {
            if let url = model.originalURL(item) {
                openURL(url)
            } else {
                model.toast = "这条没有原文链接"
            }
        } label: {
            VStack(alignment: .leading, spacing: 4) {
                Text("\(item.source)  ·  #\(item.index)")
                    .font(.system(size: 12))
                    .foregroundStyle(sub)
                Text(item.title)
                    .font(.system(size: 16, weight: .medium))
                    .foregroundStyle(textColor)
                    .multilineTextAlignment(.leading)
                if !item.body.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    Text(item.body.trimmingCharacters(in: .whitespacesAndNewlines))
                        .font(.system(size: 13))
                        .foregroundStyle(sub)
                        .lineLimit(3)
                        .multilineTextAlignment(.leading)
                }
                Text(item.link.isEmpty ? "无原文链接" : "打开原文 ›")
                    .font(.system(size: 12))
                    .foregroundStyle(item.link.isEmpty ? sub : accent)
                    .padding(.top, 4)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(12)
            .background(Color.white)
            .clipShape(RoundedRectangle(cornerRadius: 10))
            .overlay(
                RoundedRectangle(cornerRadius: 10)
                    .stroke(Color(white: 0.88), lineWidth: 1)
            )
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}
