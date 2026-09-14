import Foundation
import WatchKit

enum WatchChatRow: Identifiable, Equatable {
    case bubble(id: UUID, mine: Bool, room: String?, sender: String, body: String)
    case pm(id: UUID, from: String, body: String)
    case system(id: UUID, String)

    var id: UUID {
        switch self {
        case .bubble(let id, _, _, _, _): return id
        case .pm(let id, _, _): return id
        case .system(let id, _): return id
        }
    }
}

@MainActor
final class WatchChatViewModel: ObservableObject {
    static let quickReplies = ["嗯", "稍等", "马上回", "好的", "收到"]

    @Published var username = ""
    @Published var sshHost = ""
    @Published var sshPort = ""
    @Published var draft = ""
    @Published var rows: [WatchChatRow] = []
    @Published var status = "未连接"
    @Published var connected = false
    @Published var busy = false
    @Published var keys: DeviceKeyStore.Keys
    @Published var toast: String?
    @Published var sendTarget: SendTarget = .currentRoom("default")
    @Published var showPubkey = false

    private let session = SSHSession()
    private var wantSession = false
    private var userDisconnectRequested = false
    private var reconnectAttempt = 0
    private var reconnectTask: Task<Void, Never>?
    private var recentOutboundBody = ""
    private var recentOutboundAt: TimeInterval = 0
    private let maxRows = 80

    init() {
        keys = DeviceKeyStore.getOrCreate()
        if let u = UserDefaults.standard.string(forKey: "sshchat.username"), !u.isEmpty {
            username = u
        }
        sshHost = AppConfig.sshHost
        sshPort = String(AppConfig.sshPort)
        sendTarget = SendTargetStore.loadTarget()
    }

    var serverDisplay: String {
        let host = sshHost.trimmingCharacters(in: .whitespacesAndNewlines)
        let h = host.isEmpty ? AppConfig.defaultHost : host
        let p = AppConfig.parsePort(sshPort) ?? AppConfig.defaultPort
        return "\(h):\(p)"
    }

    func connect(clearChat: Bool = true) {
        let user = username.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !user.isEmpty else {
            toast = "请填写用户名"
            return
        }
        let host = sshHost.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !host.isEmpty else {
            toast = "请填写主机"
            return
        }
        guard let port = AppConfig.parsePort(sshPort) else {
            toast = "端口无效"
            return
        }
        AppConfig.saveServer(host: host, port: port)
        guard !busy else { return }

        userDisconnectRequested = false
        wantSession = true
        cancelReconnect()
        busy = true
        if clearChat {
            rows.removeAll()
            appendSystem("connecting…")
        }
        status = "连接中…"

        Task {
            do {
                try await session.connect(
                    host: host,
                    port: port,
                    username: user,
                    privateSeed: keys.privateSeed,
                    onLine: { [weak self] line, serverBell in
                        self?.handleIncoming(line, serverBell: serverBell)
                    },
                    onStatus: { [weak self] s in
                        Task { @MainActor in
                            self?.status = s
                            if s.hasPrefix("已连接") {
                                self?.reconnectAttempt = 0
                                self?.connected = true
                                self?.busy = false
                                self?.sendTarget = SendTargetStore.loadTarget()
                            }
                        }
                    },
                    onDisconnect: { [weak self] reason in
                        Task { @MainActor in
                            guard let self else { return }
                            self.busy = false
                            self.connected = false
                            if self.userDisconnectRequested || !self.wantSession {
                                self.status = reason.map { "断开：\($0)" } ?? "已断开"
                                return
                            }
                            self.status = reason.map { "断开：\($0)" } ?? "已断开"
                            self.scheduleReconnect(reason: reason)
                        }
                    }
                )
                UserDefaults.standard.set(user, forKey: "sshchat.username")
            } catch {
                connected = false
                busy = false
                status = "失败：\(error.localizedDescription)"
                if wantSession && !userDisconnectRequested {
                    scheduleReconnect(reason: error.localizedDescription)
                }
            }
        }
    }

    func disconnect() {
        userDisconnectRequested = true
        wantSession = false
        cancelReconnect()
        Task {
            await session.disconnect()
            connected = false
            busy = false
            status = "未连接"
        }
    }

    func sendDraft() {
        sendText(draft)
        draft = ""
    }

    func sendQuick(_ text: String) {
        sendText(text)
    }

    func setRoomTarget() {
        let room = SendTargetStore.loadCurrentRoom()
        sendTarget = .currentRoom(room)
        SendTargetStore.saveTarget(sendTarget)
        toast = "发到 #\(room)"
    }

    private func sendText(_ text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        guard connected else {
            toast = "未连接"
            return
        }
        recentOutboundBody = trimmed
        recentOutboundAt = Date().timeIntervalSince1970
        let outbound = SendTarget.outboundText(target: sendTarget, draft: trimmed)
        Task {
            do {
                try await session.send(outbound)
            } catch {
                status = "发送失败：\(error.localizedDescription)"
            }
        }
    }

    func handleBecameActive() {
        guard wantSession, !userDisconnectRequested, !connected, !busy else { return }
        cancelReconnect()
        connect(clearChat: false)
    }

    private func cancelReconnect() {
        reconnectTask?.cancel()
        reconnectTask = nil
    }

    private func scheduleReconnect(reason: String?) {
        guard wantSession, !userDisconnectRequested, !busy else { return }
        cancelReconnect()
        let attempt = min(reconnectAttempt, 5)
        let delay = min(45.0, Double(1 << attempt))
        reconnectAttempt = attempt + 1
        status = reason.map { "断开：\($0)，\(Int(delay))s…" }
            ?? "已断开，\(Int(delay))s…"
        reconnectTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
            guard !Task.isCancelled else { return }
            await MainActor.run {
                guard let self else { return }
                self.reconnectTask = nil
                guard self.wantSession, !self.userDisconnectRequested, !self.connected, !self.busy else { return }
                self.connect(clearChat: false)
            }
        }
    }

    private func handleIncoming(_ line: String, serverBell: Bool) {
        _ = serverBell
        if let room = ChatLineParsers.parseActiveRoom(line) {
            SendTargetStore.saveCurrentRoom(room)
            if case .currentRoom = sendTarget {
                sendTarget = .currentRoom(room)
            }
        }

        if let pm = ChatLineParsers.parsePm(line) {
            append(.pm(id: UUID(), from: pm.from, body: pm.body))
            buzzIfNeeded(line)
            return
        }

        switch ChatLineParsers.classifyForDisplay(line, myName: username) {
        case .bubble(let mine, let room, let sender, let body, _):
            append(.bubble(id: UUID(), mine: mine, room: room, sender: sender, body: body))
            if !mine { buzzIfNeeded(line) }
        case .system(let t):
            append(.system(id: UUID(), t))
            buzzIfNeeded(line)
        case .boardLine(let t):
            // Watch MVP: collapse board noise into a short system row.
            let preview = t.trimmingCharacters(in: .whitespacesAndNewlines)
            if !preview.isEmpty {
                append(.system(id: UUID(), preview.count > 40 ? String(preview.prefix(40)) + "…" : preview))
            }
        }
    }

    private func buzzIfNeeded(_ line: String) {
        guard ChatLineParsers.shouldAlert(
            line,
            myName: username,
            recentOutboundBody: recentOutboundBody,
            recentOutboundAt: recentOutboundAt
        ) else { return }
        WKInterfaceDevice.current().play(.notification)
    }

    private func appendSystem(_ text: String) {
        append(.system(id: UUID(), text))
    }

    private func append(_ row: WatchChatRow) {
        rows.append(row)
        if rows.count > maxRows {
            rows.removeFirst(rows.count - maxRows)
        }
    }
}
