import AVFoundation
import PhotosUI
import SwiftUI
import UIKit
import UniformTypeIdentifiers

enum ChatEntry: Identifiable, Equatable {
    case text(id: UUID, String)
    case pm(id: UUID, from: String, body: String)
    case media(DownloadedMedia)

    var id: UUID {
        switch self {
        case .text(let id, _): return id
        case .pm(let id, _, _): return id
        case .media(let m): return m.id
        }
    }
}

@MainActor
final class ChatViewModel: ObservableObject {
    @Published var username = ""
    @Published var sshHost = ""
    @Published var sshPort = ""
    @Published var draft = ""
    @Published var entries: [ChatEntry] = []
    @Published var status = "未连接"
    @Published var connected = false
    @Published var busy = false
    @Published var keys: DeviceKeyStore.Keys
    @Published var chatFont: CGFloat = 13
    @Published var suggestions: [String] = []
    @Published var mediaHint = "话筒语音 · 相机拍照/长按录像 · 文件夹 · 画板 · 垃圾桶清屏"
    @Published var recording = false
    @Published var toast: String?
    @Published var webInvite: WebInvitePayload?
    @Published var previewMedia: DownloadedMedia?
    @Published var showPhotoMenu = false
    @Published var showCameraPhoto = false
    @Published var showCameraVideo = false
    @Published var showFileImporter = false
    @Published var fileImportKind: FileImportKind = .media
    @Published var showPhotoLibrary = false
    @Published var photoItem: PhotosPickerItem?
    @Published var sendTarget: SendTarget = .currentRoom("default")
    @Published var onlineUsers: [String] = []
    @Published var showSendTargetPicker = false

    enum FileImportKind { case media, identity }

    private var expectingNames = false

    private let session = SSHSession()
    private var pendingUpload: URL?
    private var uploadWaitTask: Task<Void, Never>?
    private var voiceRecorder: VoiceRecorder?
    private var voiceFingerDown = false
    private var onConnectedOnce: (() -> Void)?
    var mediaPickerOpen = false
    private var lastSendAt: Date = .distantPast
    private var lastSendText = ""
    private let screenCleared = try! NSRegularExpression(
        pattern: #"^(?:\[[\d:.\sAPMapm/-]+]\s*)?(?:\[\*]\s*)?Screen cleared\.?\s*$"#,
        options: [.caseInsensitive]
    )
    private var pendingFileMeta = SecureInvite.FileMeta()
    private let ansiClear = try! NSRegularExpression(pattern: #"\u001B\[[0-9;]*[HJKjk]"#)

    struct WebInvitePayload: Identifiable {
        let id = UUID()
        let title: String
        let url: String
        let key: String
    }

    private static let chatFontKey = "chat_font_sp"

    var serverDisplay: String {
        let host = sshHost.trimmingCharacters(in: .whitespacesAndNewlines)
        let h = host.isEmpty ? AppConfig.defaultHost : host
        let p = AppConfig.parsePort(sshPort) ?? AppConfig.defaultPort
        return "\(h):\(p)"
    }

    init() {
        keys = DeviceKeyStore.getOrCreate()
        if let u = UserDefaults.standard.string(forKey: "sshchat.username"), !u.isEmpty {
            username = u
        }
        sshHost = AppConfig.sshHost
        sshPort = String(AppConfig.sshPort)
        if UserDefaults.standard.object(forKey: Self.chatFontKey) != nil {
            let saved = UserDefaults.standard.double(forKey: Self.chatFontKey)
            chatFont = min(22, max(7, CGFloat(saved)))
        }
        sendTarget = SendTargetStore.loadTarget()
    }

    func refreshSendTargetLabel() {
        if case .currentRoom = sendTarget {
            sendTarget = .currentRoom(SendTargetStore.loadCurrentRoom())
        }
    }

    func setSendTarget(_ target: SendTarget) {
        sendTarget = target
        SendTargetStore.saveTarget(target)
    }

    func refreshOnlineUsers() {
        expectingNames = true
        Task { try? await session.send("/names") }
    }

    func replyToPm(_ nick: String) {
        setSendTarget(.user(nick))
        toast = "已切换为私聊 \(nick)"
    }

    var keyHint: String {
        if keys.freshlyGenerated {
            return "已生成新密钥，并备份到「文件/SSHChat/\(DeviceKeyStore.durableName)」。重装后一般会自动恢复；若变成新钥，点「从备份文件恢复密钥」。"
        }
        if keys.restoredFromBackup {
            return "已恢复上次密钥，公钥不变，通常无需重新登记。"
        }
        return "本机密钥已存在；备份：App 文件/SSHChat。重装可自动恢复，或点下方手动恢复。"
    }

    func bumpFont(_ delta: CGFloat) {
        chatFont = min(22, max(7, chatFont + delta))
        UserDefaults.standard.set(Double(chatFont), forKey: Self.chatFontKey)
    }

    func copyPubkey() {
        UIPasteboard.general.string = keys.publicOpenSshLine
        toast = "公钥已复制"
    }

    func importKey(from url: URL) {
        do {
            let accessing = url.startAccessingSecurityScopedResource()
            defer { if accessing { url.stopAccessingSecurityScopedResource() } }
            let data = try Data(contentsOf: url)
            keys = try DeviceKeyStore.importFromData(data)
            toast = "密钥已恢复，公钥与重装前相同"
        } catch {
            toast = "恢复失败: \(error.localizedDescription)"
        }
    }

    func refreshSuggestions() {
        suggestions = draft.hasPrefix("/") ? Array(CommandCompletions.completions(draft).prefix(12)) : []
    }

    func applySuggestion(_ chosen: String) {
        let fill = chosen.hasSuffix(" ") ? chosen : "\(chosen) "
        draft = fill
        refreshSuggestions()
    }

    func insertSlash() {
        draft += "/"
        refreshSuggestions()
    }

    func applyTab() {
        if let next = CommandCompletions.applyTab(draft) {
            draft = next
        }
        refreshSuggestions()
    }

    func connect(clearChat: Bool = true) {
        let user = username.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !user.isEmpty else {
            toast = "请填写 Linux 用户名"
            onConnectedOnce = nil
            return
        }
        let host = sshHost.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !host.isEmpty else {
            toast = "请填写 SSH 主机"
            onConnectedOnce = nil
            return
        }
        guard let port = AppConfig.parsePort(sshPort) else {
            toast = "SSH 端口必须是 1–65535 的数字"
            onConnectedOnce = nil
            return
        }
        AppConfig.saveServer(host: host, port: port)
        guard !busy else { return }
        busy = true
        if clearChat {
            entries.removeAll()
            appendText("[*] connecting…")
        }
        status = "连接中…"
        UIApplication.shared.isIdleTimerDisabled = true

        Task {
            do {
                try await session.connect(
                    host: host,
                    port: port,
                    username: user,
                    privateSeed: keys.privateSeed,
                    onLine: { [weak self] line in
                        Task { @MainActor in
                            self?.handleIncoming(line)
                        }
                    },
                    onStatus: { [weak self] s in
                        Task { @MainActor in
                            self?.status = s
                            if s.hasPrefix("已连接") {
                                self?.connected = true
                                self?.busy = false
                                self?.sendTarget = SendTargetStore.loadTarget()
                                self?.refreshOnlineUsers()
                                let once = self?.onConnectedOnce
                                self?.onConnectedOnce = nil
                                once?()
                            }
                        }
                    },
                    onDisconnect: { [weak self] reason in
                        Task { @MainActor in
                            guard let self else { return }
                            self.busy = false
                            if self.mediaPickerOpen {
                                self.status = "拍照/选图中（返回后自动重连）…"
                                return
                            }
                            self.connected = false
                            UIApplication.shared.isIdleTimerDisabled = false
                            self.status = reason.map { "断开：\($0)" } ?? "已断开"
                        }
                    }
                )
                UserDefaults.standard.set(user, forKey: "sshchat.username")
            } catch {
                connected = false
                busy = false
                UIApplication.shared.isIdleTimerDisabled = false
                onConnectedOnce = nil
                status = "连接失败：\(error.localizedDescription)"
            }
        }
    }

    func disconnect() {
        mediaPickerOpen = false
        onConnectedOnce = nil
        cancelUploadWait()
        pendingUpload = nil
        voiceRecorder?.cancel()
        voiceRecorder = nil
        recording = false
        mediaHint = "话筒语音 · 相机拍照/长按录像 · 文件夹 · 画板 · 垃圾桶清屏"
        Task {
            await session.disconnect()
            connected = false
            busy = false
            UIApplication.shared.isIdleTimerDisabled = false
            status = "未连接"
        }
    }

    func send() {
        let text = draft
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        let now = Date()
        if text == lastSendText && now.timeIntervalSince(lastSendAt) < 0.5 { return }
        lastSendText = text
        lastSendAt = now
        draft = ""
        suggestions = []
        let cmd = text.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if cmd == "/cls" || cmd == "/clear" {
            clearScreen(announce: false)
            Task { try? await session.send(text.trimmingCharacters(in: .whitespacesAndNewlines)) }
            return
        }
        Task {
            do {
                let outbound = SendTarget.outboundText(target: sendTarget, draft: text)
                try await session.send(outbound)
            } catch {
                status = "发送失败：\(error.localizedDescription)"
            }
        }
    }

    private func sendfileCommand() -> String {
        sendTarget.sendfileCommand
    }

    func clearScreen(announce: Bool) {
        entries.removeAll()
        if announce { appendText("[*] Screen cleared.") }
    }

    func startCanvas() {
        guard connected else { toast = "请先连接"; return }
        appendText("[*] 正在创建共享画板…（/canvas）")
        Task { try? await session.send("/canvas") }
    }

    func beginSendFile(_ url: URL) {
        guard connected || onConnectedOnce != nil else {
            toast = "请先连接"
            return
        }
        pendingUpload = url
        status = "等待上传通道…"
        appendText("[*] 正在发文件: \(url.lastPathComponent)（\(sendfileCommand())）")
        scheduleUploadWaitTimeout()
        Task { try? await session.send(sendfileCommand()) }
    }

    private func cancelUploadWait() {
        uploadWaitTask?.cancel()
        uploadWaitTask = nil
    }

    private func scheduleUploadWaitTimeout() {
        cancelUploadWait()
        uploadWaitTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 45_000_000_000)
            guard !Task.isCancelled else { return }
            await MainActor.run {
                guard let self else { return }
                if self.pendingUpload != nil {
                    self.pendingUpload = nil
                    self.status = "发文件失败"
                    self.appendText("[*] 发文件失败: 等待上传通道超时")
                }
            }
        }
    }

    func ensureConnectedThen(_ block: @escaping () -> Void) {
        if connected {
            block()
            return
        }
        let user = username.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !user.isEmpty else {
            toast = "连接已断，请重新登录"
            connected = false
            return
        }
        appendText("[*] 拍照/录像/选图期间连接中断，正在重连…")
        onConnectedOnce = block
        connect(clearChat: false)
    }

    func pickAndSendFile() {
        guard connected else { toast = "请先连接"; return }
        if pendingUpload != nil { toast = "已有文件正在上传，请稍候"; return }
        mediaPickerOpen = true
        fileImportKind = .media
        showFileImporter = true
    }

    func openKeyImporter() {
        fileImportKind = .identity
        showFileImporter = true
    }

    func openPhotoMenu() {
        guard connected else { toast = "请先连接"; return }
        if pendingUpload != nil { toast = "已有文件正在上传，请稍候"; return }
        showPhotoMenu = true
    }

    func launchCameraPhoto() {
        mediaPickerOpen = true
        showCameraPhoto = true
    }

    func launchCameraVideo() {
        guard connected else { toast = "请先连接"; return }
        if pendingUpload != nil { toast = "已有文件正在上传，请稍候"; return }
        mediaPickerOpen = true
        showCameraVideo = true
    }

    func launchPhotoLibrary() {
        mediaPickerOpen = true
        showPhotoLibrary = true
    }

    func handlePickedMedia(_ url: URL) {
        mediaPickerOpen = false
        ensureConnectedThen { [weak self] in
            self?.beginSendFile(url)
        }
    }

    func handlePickedFile(_ url: URL) {
        mediaPickerOpen = false
        let accessing = url.startAccessingSecurityScopedResource()
        defer { if accessing { url.stopAccessingSecurityScopedResource() } }
        do {
            let dir = FileManager.default.temporaryDirectory.appendingPathComponent("sshchat-media", isDirectory: true)
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            let safe = url.lastPathComponent.replacingOccurrences(of: "/", with: "_")
            let out = dir.appendingPathComponent("\(Int(Date().timeIntervalSince1970 * 1000))-\(safe)")
            if FileManager.default.fileExists(atPath: out.path) {
                try FileManager.default.removeItem(at: out)
            }
            try FileManager.default.copyItem(at: url, to: out)
            ensureConnectedThen { [weak self] in self?.beginSendFile(out) }
        } catch {
            toast = "读取失败: \(error.localizedDescription)"
        }
    }

    func handlePhotoItem() {
        guard let item = photoItem else { return }
        photoItem = nil
        mediaPickerOpen = false
        Task {
            do {
                if let data = try await item.loadTransferable(type: Data.self) {
                    let dir = FileManager.default.temporaryDirectory.appendingPathComponent("sshchat-media", isDirectory: true)
                    try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
                    let out = dir.appendingPathComponent("pick-\(Int(Date().timeIntervalSince1970 * 1000)).jpg")
                    try data.write(to: out)
                    ensureConnectedThen { [weak self] in self?.beginSendFile(out) }
                }
            } catch {
                toast = "读取失败: \(error.localizedDescription)"
            }
        }
    }

    func voicePressBegan() {
        guard connected else { toast = "请先连接"; return }
        if pendingUpload != nil { toast = "已有文件正在上传，请稍候"; return }
        if recording { return }
        voiceFingerDown = true

        let startIfStillHolding = { [weak self] in
            guard let self, self.voiceFingerDown, !self.recording else { return }
            self.startVoiceRecord()
        }

        switch VoicePermission.status() {
        case .granted:
            startIfStillHolding()
        case .denied:
            voiceFingerDown = false
            toast = "需要麦克风权限才能发语音（请在「设置 → SSHChat → 麦克风」中开启）"
        case .undetermined:
            VoicePermission.request { [weak self] ok in
                guard let self else { return }
                guard ok else {
                    self.voiceFingerDown = false
                    self.toast = "需要麦克风权限才能发语音"
                    return
                }
                startIfStillHolding()
            }
        @unknown default:
            voiceFingerDown = false
        }
    }

    func voicePressEnded(send: Bool) {
        voiceFingerDown = false
        finishVoiceRecord(send: send)
    }

    private func startVoiceRecord() {
        do {
            let dir = FileManager.default.temporaryDirectory.appendingPathComponent("sshchat-voice", isDirectory: true)
            let rec = VoiceRecorder()
            _ = try rec.start(outDir: dir)
            voiceRecorder = rec
            recording = true
            status = "正在录音…"
            mediaHint = "松开手指发送"
        } catch {
            voiceRecorder = nil
            recording = false
            toast = "无法录音: \(error.localizedDescription)"
        }
    }

    private func finishVoiceRecord(send: Bool) {
        guard let rec = voiceRecorder else { return }
        voiceRecorder = nil
        recording = false
        mediaHint = "话筒语音 · 相机拍照/长按录像 · 文件夹 · 画板 · 垃圾桶清屏"
        if !send {
            rec.cancel()
            status = "已取消录音"
            return
        }
        guard let file = rec.stop(minMs: 400) else {
            status = "录音太短"
            toast = "录音太短"
            return
        }
        ensureConnectedThen { [weak self] in self?.beginSendFile(file) }
    }

    private func handleIncoming(_ line: String) {
        if PtyNoise.shouldDrop(line) { return }
        let stripped = ansiClear.stringByReplacingMatches(
            in: line,
            range: NSRange(line.startIndex..., in: line),
            withTemplate: ""
        ).trimmingCharacters(in: .whitespacesAndNewlines)
        if stripped.isEmpty { return }

        let clearRange = NSRange(stripped.startIndex..., in: stripped)
        if screenCleared.firstMatch(in: stripped, range: clearRange) != nil
            || stripped.compare("Screen cleared.", options: .caseInsensitive) == .orderedSame
            || stripped.compare("[*] Screen cleared.", options: .caseInsensitive) == .orderedSame
        {
            clearScreen(announce: false)
            appendText("[*] Screen cleared.")
            return
        }

        applyRoomFromServer(stripped)
        if expectingNames, let names = ChatLineParsers.parseNames(stripped) {
            onlineUsers = names.members
            expectingNames = false
            if case .currentRoom = sendTarget {
                sendTarget = .currentRoom(names.room)
                SendTargetStore.saveCurrentRoom(names.room)
            }
            return
        }
        if let pm = ChatLineParsers.parsePm(stripped) {
            appendPm(from: pm.from, body: pm.body)
            return
        }

        SecureInvite.absorbFileMeta(stripped, into: &pendingFileMeta)

        if let open = SecureInvite.parseGuiOpen(stripped) {
            switch open.kind {
            case .download:
                let from = pendingFileMeta.sender
                pendingFileMeta.reset()
                startDownload(url: open.url, key: open.key, sender: from)
            case .canvas:
                appendText("[*] 打开共享画布…")
                webInvite = WebInvitePayload(title: "共享画布", url: open.url, key: open.key)
            case .upload:
                if let pending = pendingUpload {
                    cancelUploadWait()
                    pendingUpload = nil
                    startUpload(url: open.url, key: open.key, file: pending)
                } else {
                    appendText("[*] 打开上传页…")
                    webInvite = WebInvitePayload(title: "上传文件", url: open.url, key: open.key)
                }
            }
            return
        }

        if pendingUpload != nil, SecureInvite.isSendfileFailure(stripped) {
            cancelUploadWait()
            pendingUpload = nil
            appendText(stripped)
            status = "发文件失败"
            return
        }

        SecureInvite.absorbFileMeta(stripped, into: &pendingFileMeta)
        if SecureInvite.isInviteNoise(stripped) { return }
        appendText(stripped)
    }

    private func startUpload(url: String, key: String, file: URL) {
        cancelUploadWait()
        status = "上传中: \(file.lastPathComponent)"
        appendText("[*] 上传中: \(file.lastPathComponent)")
        Task {
            do {
                let remote = try await SecureUpload.upload(url: url, key: key, fileURL: file)
                let mime = MediaMime.guess(remote.isEmpty ? file.lastPathComponent : remote)
                let media = DownloadedMedia(
                    url: file,
                    name: remote.isEmpty ? file.lastPathComponent : remote,
                    mime: mime,
                    sender: username.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : username
                )
                status = "已上传: \(media.name)"
                appendMedia(media, autoOpen: media.isImage || media.isVideo)
            } catch {
                status = "发文件失败"
                appendText("[*] 发文件失败（\(file.lastPathComponent)）: \(error.localizedDescription)")
            }
        }
    }

    private func startDownload(url: String, key: String, sender: String?) {
        status = "正在接收文件…"
        let from = sender?.trimmingCharacters(in: .whitespacesAndNewlines)
        Task {
            do {
                let dir = FileManager.default.temporaryDirectory.appendingPathComponent("sshchat-media", isDirectory: true)
                var media = try await SecureDownload.fetch(pageURL: url, key: key, outDir: dir)
                media.sender = from?.isEmpty == false ? from : nil
                status = "已接收: \(media.name)"
                appendMedia(media, autoOpen: media.isImage || media.isVideo || media.isAudio)
            } catch {
                status = "收文件失败"
                appendText("[*] 收文件失败: \(error.localizedDescription)")
            }
        }
    }

    private func applyRoomFromServer(_ line: String) {
        guard let room = ChatLineParsers.parseActiveRoom(line) else { return }
        if pendingUpload != nil {
            cancelUploadWait()
            pendingUpload = nil
            status = "已切换房间，取消待发文件"
        }
        SendTargetStore.saveCurrentRoom(room)
        if case .currentRoom = sendTarget {
            sendTarget = .currentRoom(room)
            SendTargetStore.saveTarget(sendTarget)
        }
    }

    private func appendPm(from: String, body: String) {
        if entries.count > 2000 {
            entries.removeFirst(entries.count - 1500)
        }
        entries.append(.pm(id: UUID(), from: from, body: body))
    }

    private func appendText(_ line: String) {
        // Cap history so SwiftUI stays responsive on long sessions.
        if entries.count > 2000 {
            entries.removeFirst(entries.count - 1500)
        }
        entries.append(.text(id: UUID(), line))
    }

    private func appendMedia(_ media: DownloadedMedia, autoOpen: Bool) {
        entries.append(.media(media))
        if autoOpen {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.08) { [weak self] in
                if FileManager.default.fileExists(atPath: media.url.path) {
                    self?.previewMedia = media
                }
            }
        }
    }

    static func formatSize(_ bytes: Int64) -> String {
        if bytes < 1024 { return "\(bytes) B" }
        if bytes < 1024 * 1024 { return String(format: "%.1f KB", Double(bytes) / 1024) }
        return String(format: "%.1f MB", Double(bytes) / (1024 * 1024))
    }
}

struct ContentView: View {
    @StateObject private var model = ChatViewModel()
    @FocusState private var draftFocused: Bool
    @State private var showDisconnectConfirm = false

    var body: some View {
        VStack(spacing: 0) {
            topBar
            VStack(spacing: 0) {
                if !model.connected {
                    loginPanel
                }
                if model.connected {
                    sendTargetRow
                }
                Text(model.status)
                    .font(.system(size: 12))
                    .foregroundStyle(Color(white: 0.4))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.top, 4)
                Text("棋盘可左右滑；顶栏 A- / A+ 调字号")
                    .font(.system(size: 11))
                    .foregroundStyle(Color(white: 0.53))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.top, 2)

                chatLog
                    .frame(maxWidth: .infinity, maxHeight: .infinity)

                suggestRow
                draftRow
                actionRow
                Text(model.mediaHint)
                    .font(.system(size: 11))
                    .foregroundStyle(Color(white: 0.53))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.top, 2)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .background(Color.white)
        .alert("确认断开？", isPresented: $showDisconnectConfirm) {
            Button("断开", role: .destructive) { model.disconnect() }
            Button("取消", role: .cancel) {}
        } message: {
            Text("断开后将退出当前聊天连接。")
        }
        .confirmationDialog("发图", isPresented: $model.showPhotoMenu, titleVisibility: .visible) {
            Button("拍照发送") { model.launchCameraPhoto() }
            Button("从相册选择") { model.launchPhotoLibrary() }
            Button("录像发送") { model.launchCameraVideo() }
            Button("取消", role: .cancel) {}
        }
        .sheet(isPresented: $model.showCameraPhoto) {
            CameraPicker(mode: .photo, onPicked: { url in
                model.showCameraPhoto = false
                model.handlePickedMedia(url)
            }, onCancel: {
                model.showCameraPhoto = false
                model.mediaPickerOpen = false
            })
            .ignoresSafeArea()
        }
        .sheet(isPresented: $model.showCameraVideo) {
            CameraPicker(mode: .video, onPicked: { url in
                model.showCameraVideo = false
                model.handlePickedMedia(url)
            }, onCancel: {
                model.showCameraVideo = false
                model.mediaPickerOpen = false
            })
            .ignoresSafeArea()
        }
        .photosPicker(isPresented: $model.showPhotoLibrary, selection: $model.photoItem, matching: .images)
        .onChange(of: model.photoItem) { _, _ in model.handlePhotoItem() }
        .fileImporter(isPresented: $model.showFileImporter, allowedContentTypes: [.item, .plainText, .data], allowsMultipleSelection: false) { result in
            switch model.fileImportKind {
            case .media:
                model.mediaPickerOpen = false
                if case .success(let urls) = result, let url = urls.first {
                    model.handlePickedFile(url)
                }
            case .identity:
                if case .success(let urls) = result, let url = urls.first {
                    model.importKey(from: url)
                }
            }
        }
        .sheet(item: $model.webInvite) { invite in
            WebInviteView(title: invite.title, url: invite.url, key: invite.key)
        }
        .sheet(item: $model.previewMedia) { media in
            MediaPreviewView(media: media)
        }
        .sheet(isPresented: $model.showSendTargetPicker) {
            SendTargetPickerView(model: model)
        }
        .overlay(alignment: .bottom) {
            if let toast = model.toast {
                Text(toast)
                    .font(.footnote)
                    .padding(10)
                    .background(.black.opacity(0.8))
                    .foregroundStyle(.white)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                    .padding(.bottom, 24)
                    .onAppear {
                        DispatchQueue.main.asyncAfter(deadline: .now() + 2) {
                            if model.toast == toast { model.toast = nil }
                        }
                    }
            }
        }
        .overlay {
            if model.recording {
                voiceRecordingOverlay
            }
        }
    }

    private var voiceRecordingOverlay: some View {
        VStack(spacing: 12) {
            Image(systemName: "mic.fill")
                .font(.system(size: 48))
                .foregroundStyle(.red)
            Text("正在录音")
                .font(.system(size: 24, weight: .semibold))
            Text("松开手指发送")
                .font(.system(size: 17))
        }
        .foregroundStyle(.white)
        .padding(.horizontal, 36)
        .padding(.vertical, 28)
        .background(.black.opacity(0.82))
        .clipShape(RoundedRectangle(cornerRadius: 16))
        .shadow(color: .black.opacity(0.25), radius: 12, y: 4)
        .allowsHitTesting(false)
    }

    private var topBar: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack {
                Text("SSHChat")
                    .font(.system(size: 20, weight: .bold))
                    .foregroundStyle(.white)
                Spacer()
                Button("A-") { model.bumpFont(-1) }
                    .foregroundStyle(.white)
                    .fontWeight(.bold)
                Button("A+") { model.bumpFont(1) }
                    .foregroundStyle(.white)
                    .fontWeight(.bold)
            }
            Text(model.serverDisplay)
                .font(.system(size: 12))
                .foregroundStyle(Color(red: 0.78, green: 0.90, blue: 0.79))
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .background(Color(red: 0.106, green: 0.369, blue: 0.125))
    }

    private var loginPanel: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(model.keyHint)
                .font(.system(size: 12))
                .foregroundStyle(.primary)
            Text(model.keys.publicOpenSshLine)
                .font(.system(size: 11, design: .monospaced))
                .padding(8)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color(white: 0.94))
                .textSelection(.enabled)
            Button("复制公钥") { model.copyPubkey() }
                .buttonStyle(.borderedProminent)
                .frame(maxWidth: .infinity)
            Button("从备份文件恢复密钥") { model.openKeyImporter() }
                .frame(maxWidth: .infinity)
            TextField("SSH 主机", text: $model.sshHost)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .keyboardType(.URL)
                .textFieldStyle(.roundedBorder)
            TextField("SSH 端口", text: $model.sshPort)
                .keyboardType(.numberPad)
                .textFieldStyle(.roundedBorder)
            TextField("Linux 用户名", text: $model.username)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .textFieldStyle(.roundedBorder)
            Button {
                model.connect()
            } label: {
                HStack {
                    Spacer()
                    if model.busy { ProgressView() } else { Text("连接").fontWeight(.semibold) }
                    Spacer()
                }
            }
            .buttonStyle(.borderedProminent)
            .disabled(model.busy)
        }
    }

    private var chatLog: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(model.entries) { entry in
                        chatRow(entry)
                            .id(entry.id)
                    }
                }
                .padding(8)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .background(Color(white: 0.98))
            .onChange(of: model.entries.count) { _, _ in
                if let last = model.entries.last?.id {
                    DispatchQueue.main.async {
                        proxy.scrollTo(last, anchor: .bottom)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func chatRow(_ entry: ChatEntry) -> some View {
        switch entry {
        case .text(_, let line):
            Text(line)
                .font(.system(size: model.chatFont, design: .monospaced))
                .foregroundStyle(Color(white: 0.13))
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
        case .pm(_, let from, let body):
            Button {
                model.replyToPm(from)
            } label: {
                VStack(alignment: .leading, spacing: 4) {
                    Text("私聊 · \(from)")
                        .font(.system(size: max(10, model.chatFont - 1), weight: .semibold))
                        .foregroundStyle(Color(red: 0.082, green: 0.396, blue: 0.753))
                    Text(body)
                        .font(.system(size: model.chatFont, design: .monospaced))
                        .foregroundStyle(Color(white: 0.13))
                        .frame(maxWidth: .infinity, alignment: .leading)
                    Text("点按回复")
                        .font(.system(size: max(10, model.chatFont - 1)))
                        .foregroundStyle(Color(red: 0.043, green: 0.341, blue: 0.816))
                        .underline()
                }
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color(red: 0.89, green: 0.95, blue: 0.99))
                .clipShape(RoundedRectangle(cornerRadius: 8))
            }
            .buttonStyle(.plain)
        case .media(let media):
            mediaCard(media)
        }
    }

    private func mediaCard(_ media: DownloadedMedia) -> some View {
        let size = (try? FileManager.default.attributesOfItem(atPath: media.url.path)[.size] as? NSNumber)?.int64Value ?? 0
        let kind = MediaMime.kindLabel(mime: media.mime, name: media.name)
        let action: String = {
            if media.isAudio { return "点按播放" }
            if media.isVideo { return "点按播放" }
            if media.isImage { return "点按再次查看" }
            return "点按打开"
        }()
        let headline: String = {
            var s = ""
            if let who = media.sender, !who.isEmpty { s += "来自 \(who) · " }
            s += "[\(kind)] \(media.name) (\(ChatViewModel.formatSize(size)))"
            return s
        }()
        return Button {
            model.previewMedia = media
        } label: {
            VStack(alignment: .leading, spacing: 4) {
                Text(headline)
                    .font(.system(size: model.chatFont, weight: .bold, design: .monospaced))
                    .foregroundStyle(Color(red: 0.106, green: 0.369, blue: 0.125))
                if media.isImage, let ui = UIImage(contentsOfFile: media.url.path) {
                    Image(uiImage: ui)
                        .resizable()
                        .scaledToFit()
                        .frame(maxHeight: 160)
                }
                Text(action)
                    .font(.system(size: max(10, model.chatFont - 1)))
                    .foregroundStyle(Color(red: 0.043, green: 0.341, blue: 0.816))
                    .underline()
            }
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(red: 0.91, green: 0.96, blue: 0.91))
        }
        .buttonStyle(.plain)
    }

    private var suggestRow: some View {
        Group {
            if !model.suggestions.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 6) {
                        ForEach(model.suggestions, id: \.self) { item in
                            Button(item) { model.applySuggestion(item) }
                                .font(.system(size: 11))
                                .buttonStyle(.bordered)
                        }
                    }
                    .padding(.vertical, 4)
                }
            }
        }
    }

    private var sendTargetRow: some View {
        HStack(spacing: 8) {
            Text("发送至")
                .font(.system(size: 12))
                .foregroundStyle(Color(white: 0.33))
            Button {
                model.showSendTargetPicker = true
            } label: {
                Text(model.sendTarget.label)
                    .font(.system(size: 12))
                    .lineLimit(1)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .buttonStyle(.bordered)
            .disabled(!model.connected)
        }
        .padding(.top, 4)
    }

    private var draftRow: some View {
        TextField("消息或 /命令（点候选补全）", text: $model.draft)
            .textInputAutocapitalization(.never)
            .autocorrectionDisabled()
            .textFieldStyle(.roundedBorder)
            .focused($draftFocused)
            .disabled(!model.connected)
            .onChange(of: model.draft) { _, _ in model.refreshSuggestions() }
            .onSubmit { model.send() }
            .padding(.top, 6)
    }

    private var actionRow: some View {
        HStack(spacing: 4) {
            PushToTalkButton(
                active: model.recording,
                enabled: model.connected,
                onPressBegan: { model.voicePressBegan() },
                onPressEnded: { send in model.voicePressEnded(send: send) }
            )
            .frame(maxWidth: .infinity)
            .frame(height: 44)

            iconBtn(system: "camera.fill", enabled: model.connected) {
                model.openPhotoMenu()
            }

            iconBtn(system: "folder.fill", enabled: model.connected) { model.pickAndSendFile() }
            iconBtn(system: "paintbrush.pointed.fill", enabled: model.connected) { model.startCanvas() }
            iconBtn(system: "trash", enabled: true) { model.clearScreen(announce: true) }

            Button("/") { model.insertSlash() }
                .font(.system(size: 18, weight: .bold))
                .frame(maxWidth: .infinity, minHeight: 44)
                .disabled(!model.connected)
            Button("Tab") { model.applyTab() }
                .font(.system(size: 12))
                .frame(maxWidth: .infinity, minHeight: 44)
                .disabled(!model.connected)
            Button("发送") { model.send() }
                .font(.system(size: 12))
                .frame(maxWidth: .infinity, minHeight: 44)
                .disabled(!model.connected)
            Button("断开") { showDisconnectConfirm = true }
                .font(.system(size: 12))
                .frame(maxWidth: .infinity, minHeight: 44)
                .disabled(!model.connected)
        }
        .padding(.top, 4)
    }

    private func iconBtn(system: String, active: Bool = false, enabled: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: system)
                .font(.system(size: 18))
                .foregroundStyle(active ? Color.red : Color.primary)
                .frame(maxWidth: .infinity, minHeight: 44)
                .background(Color(white: 0.94))
                .clipShape(RoundedRectangle(cornerRadius: 8))
                .opacity(enabled ? 1 : 0.35)
        }
        .disabled(!enabled && !active)
        .buttonStyle(.plain)
    }
}

#Preview {
    ContentView()
}
