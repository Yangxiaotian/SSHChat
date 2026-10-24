import AVFoundation
import MediaPlayer
import SwiftUI

/// Process-lifetime player so dismissing / Home does not tear down audio.
final class MusicPlayback: ObservableObject {
    static let shared = MusicPlayback()

    @Published private(set) var songs: [Song] = []
    @Published private(set) var index: Int = 0
    @Published private(set) var playing = false
    @Published private(set) var status = ""
    @Published private(set) var loading = false
    /// Full player is on screen. Mini bar shows when this is false but a session exists.
    @Published private(set) var panelOpen = false

    struct Song: Identifiable, Equatable {
        var id: String { origin.isEmpty ? name : "\(name)@\(origin)" }
        let name: String
        let size: String
        let origin: String
        var label: String { origin.isEmpty ? name : "\(name) @\(origin)" }
    }

    var hasSession: Bool { !songs.isEmpty }
    var pageURLString: String? { pageURL?.absoluteString }
    private(set) var key = ""

    var current: Song? {
        songs.indices.contains(index) ? songs[index] : nil
    }

    private let player = AVPlayer()
    private var endObserver: Any?
    private var failObserver: Any?
    private var remoteReady = false
    private var pageURL: URL?
    private var token = ""
    private var playTask: Task<Void, Never>?
    private var localFile: URL?
    /// Fired once when URLSession cannot reach the player (LAN / dead tunnel).
    var onWebFallback: (() -> Void)?

    /// URLSession that waits through the Local Network permission sheet.
    private lazy var http: URLSession = {
        let cfg = URLSessionConfiguration.ephemeral
        cfg.waitsForConnectivity = true
        cfg.timeoutIntervalForRequest = 30
        cfg.timeoutIntervalForResource = 120
        return URLSession(configuration: cfg)
    }()

    private init() {
        endObserver = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemDidPlayToEndTime,
            object: nil,
            queue: .main
        ) { [weak self] note in
            guard let self, note.object as? AVPlayerItem === self.player.currentItem else { return }
            self.next()
        }
        failObserver = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemFailedToPlayToEndTime,
            object: nil,
            queue: .main
        ) { [weak self] note in
            guard let self, note.object as? AVPlayerItem === self.player.currentItem else { return }
            let err = note.userInfo?[AVPlayerItemFailedToPlayToEndTimeErrorKey] as? Error
            self.playing = false
            self.status = err?.localizedDescription ?? "播放失败"
        }
    }

    func setPanelOpen(_ on: Bool) {
        panelOpen = on
    }

    func open(pageURL: String, key: String) {
        status = ""
        loading = true
        panelOpen = true
        guard let page = URL(string: pageURL),
              let tok = Self.token(from: page)
        else {
            loading = false
            status = "无效点歌地址"
            return
        }
        self.pageURL = page
        self.key = key
        // Same token + already playing: keep audio, but re-auth so a late
        // federation catalog merge shows up (minimize → reopen used to skip).
        let keepPlayback = token == tok && !songs.isEmpty && playing
        token = tok
        Task { await authorizeAndPlay(page: page, key: key, restartIfNeeded: !keepPlayback) }
    }

    func close() {
        playTask?.cancel()
        playTask = nil
        player.pause()
        player.replaceCurrentItem(with: nil)
        playing = false
        songs = []
        index = 0
        status = ""
        panelOpen = false
        token = ""
        key = ""
        pageURL = nil
        if let localFile {
            try? FileManager.default.removeItem(at: localFile)
        }
        localFile = nil
        MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }

    func playAt(_ i: Int) {
        guard !songs.isEmpty else { return }
        index = (i % songs.count + songs.count) % songs.count
        guard let page = pageURL, let file = Self.fileURL(page: page, token: token, name: songs[index].name, origin: songs[index].origin) else {
            status = "无法播放"
            return
        }
        playTask?.cancel()
        playTask = Task { await fetchAndPlay(file, name: songs[index].name) }
    }

    func toggle() {
        if playing {
            player.pause()
            playing = false
        } else {
            activateSession()
            player.play()
            playing = true
        }
        publishNowPlaying()
    }

    func next() { playAt(index + 1) }
    func prev() { playAt(index - 1) }

    private func authorizeAndPlay(page: URL, key: String, restartIfNeeded: Bool = true) async {
        guard let authURL = Self.authURL(from: page) else {
            await MainActor.run {
                loading = false
                status = "无效点歌地址"
            }
            return
        }
        var req = URLRequest(url: authURL)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["key": key.uppercased()])
        do {
            let (data, resp) = try await http.data(for: req)
            let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            let obj = (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
            await MainActor.run {
                loading = false
                guard code == 200, obj["ok"] as? Bool == true else {
                    status = "密钥不正确或点歌已关闭"
                    return
                }
                let raw = obj["songs"] as? [[String: Any]] ?? []
                let prevId = current?.id
                songs = raw.compactMap { row in
                    guard let name = row["name"] as? String, !name.isEmpty else { return nil }
                    return Song(
                        name: name,
                        size: row["size"] as? String ?? "",
                        origin: row["origin"] as? String ?? ""
                    )
                }
                if songs.isEmpty {
                    status = "曲库是空的"
                    return
                }
                if restartIfNeeded {
                    let start = max(1, (obj["start"] as? NSNumber)?.intValue
                        ?? (obj["start"] as? Int)
                        ?? 1)
                    index = min(max(0, start - 1), songs.count - 1)
                    playAt(index)
                } else if let prevId,
                          let keep = songs.firstIndex(where: { $0.id == prevId }) {
                    index = keep
                } else {
                    index = min(index, songs.count - 1)
                }
            }
        } catch {
            await MainActor.run {
                loading = false
                let ns = error as NSError
                let host = page.host ?? page.absoluteString
                // -1022 = ATS; -1004/-1009/-1020 = unreachable / local-network denied
                if ns.domain == NSURLErrorDomain, ns.code == -1022 {
                    status = "无法连接点歌服务（系统拦截了 HTTP）"
                } else if Self.isConnectFailure(ns) {
                    status = "无法连接 \(host)，改用网页播放…"
                    let fb = onWebFallback
                    onWebFallback = nil
                    token = ""
                    songs = []
                    pageURL = nil
                    fb?()
                } else {
                    status = "无法连接点歌服务（\(ns.localizedDescription)）"
                }
            }
        }
    }

    private static func isConnectFailure(_ ns: NSError) -> Bool {
        guard ns.domain == NSURLErrorDomain else { return false }
        switch ns.code {
        case NSURLErrorCannotConnectToHost,
             NSURLErrorNetworkConnectionLost,
             NSURLErrorNotConnectedToInternet,
             NSURLErrorTimedOut,
             NSURLErrorDataNotAllowed,
             -1020: // local network denied (undocumented alias on some OS)
            return true
        default:
            return false
        }
    }

    private static func authURL(from page: URL) -> URL? {
        guard var comps = URLComponents(url: page, resolvingAgainstBaseURL: false) else {
            return nil
        }
        comps.query = nil
        comps.fragment = nil
        var path = comps.path
        while path.hasSuffix("/") { path = String(path.dropLast()) }
        comps.path = path + "/auth"
        return comps.url
    }

    private func fetchAndPlay(_ remote: URL, name: String) async {
        await MainActor.run {
            status = "正在加载…"
            loading = false
        }
        do {
            var req = URLRequest(url: remote)
            req.cachePolicy = .reloadIgnoringLocalCacheData
            let (tmp, resp) = try await http.download(for: req)
            if Task.isCancelled { return }
            let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            guard (200 ..< 300).contains(code) else {
                await MainActor.run {
                    status = code == 0
                        ? "无法下载 \(name)"
                        : "无法下载 \(name)（HTTP \(code)）"
                }
                return
            }
            let ext = (name as NSString).pathExtension
            let dest = FileManager.default.temporaryDirectory
                .appendingPathComponent("sshchat-music-\(UUID().uuidString).\(ext.isEmpty ? "mp3" : ext)")
            try? FileManager.default.removeItem(at: dest)
            try FileManager.default.moveItem(at: tmp, to: dest)
            if Task.isCancelled {
                try? FileManager.default.removeItem(at: dest)
                return
            }
            await MainActor.run {
                if let prev = self.localFile {
                    try? FileManager.default.removeItem(at: prev)
                }
                self.localFile = dest
                activateSession()
                let item = AVPlayerItem(url: dest)
                player.replaceCurrentItem(with: item)
                player.play()
                playing = true
                status = ""
                publishNowPlaying()
            }
        } catch is CancellationError {
            return
        } catch {
            await MainActor.run { status = error.localizedDescription }
        }
    }

    private func activateSession() {
        let session = AVAudioSession.sharedInstance()
        try? session.setActive(false, options: .notifyOthersOnDeactivation)
        // Extra options (.allowAirPlay / A2DP) return OSStatus -50 on some iPads.
        do {
            try session.setCategory(.playback, mode: .default)
            try session.setActive(true)
        } catch {
            try? session.setCategory(.playback)
            try? session.setActive(true)
        }
        UIApplication.shared.beginReceivingRemoteControlEvents()
        guard !remoteReady else { return }
        remoteReady = true
        let cc = MPRemoteCommandCenter.shared()
        cc.playCommand.isEnabled = true
        cc.pauseCommand.isEnabled = true
        cc.nextTrackCommand.isEnabled = true
        cc.previousTrackCommand.isEnabled = true
        cc.playCommand.addTarget { [weak self] _ in
            self?.player.play()
            self?.playing = true
            self?.publishNowPlaying()
            return .success
        }
        cc.pauseCommand.addTarget { [weak self] _ in
            self?.player.pause()
            self?.playing = false
            self?.publishNowPlaying()
            return .success
        }
        cc.nextTrackCommand.addTarget { [weak self] _ in
            self?.next()
            return .success
        }
        cc.previousTrackCommand.addTarget { [weak self] _ in
            self?.prev()
            return .success
        }
    }

    private func publishNowPlaying() {
        var info: [String: Any] = [
            MPMediaItemPropertyTitle: current?.label ?? "SSHChat",
            MPMediaItemPropertyArtist: current?.origin.isEmpty == false ? (current?.origin ?? "SSHChat") : "SSHChat",
            MPNowPlayingInfoPropertyPlaybackRate: playing ? 1.0 : 0.0,
        ]
        if let item = player.currentItem {
            let dur = item.duration.seconds
            if dur.isFinite, dur > 0 {
                info[MPMediaItemPropertyPlaybackDuration] = dur
            }
            let t = player.currentTime().seconds
            if t.isFinite {
                info[MPNowPlayingInfoPropertyElapsedPlaybackTime] = t
            }
        }
        MPNowPlayingInfoCenter.default().nowPlayingInfo = info
    }

    private static func token(from page: URL) -> String? {
        let parts = page.path.split(separator: "/").map(String.init)
        guard parts.count >= 2, parts[0] == "music" else { return nil }
        let tok = parts[1]
        return tok.isEmpty ? nil : tok
    }

    private static func fileURL(page: URL, token: String, name: String, origin: String = "") -> URL? {
        guard var comps = URLComponents(url: page, resolvingAgainstBaseURL: false) else { return nil }
        comps.query = nil
        comps.fragment = nil
        // Assign percentEncodedPath ourselves. Setting `path` to an already
        // encoded 星际穿越(Cornfield Chase).mp3 double-encodes and 404s.
        comps.percentEncodedPath = "/music/\(token)/file/\(encodePathSegment(name))"
        if !origin.isEmpty {
            comps.percentEncodedQuery = "origin=\(encodePathSegment(origin))"
        }
        return comps.url
    }

    private static func encodePathSegment(_ name: String) -> String {
        var allowed = CharacterSet.alphanumerics
        allowed.insert(charactersIn: "-._~!$&'()*+,;=:@")
        return name.addingPercentEncoding(withAllowedCharacters: allowed) ?? name
    }
}

struct MusicPlayerView: View {
    let pageURL: String
    let key: String
    var onMinimize: () -> Void
    var onClose: () -> Void
    var onWebFallback: (() -> Void)? = nil
    @ObservedObject private var playback = MusicPlayback.shared

    var body: some View {
        NavigationStack {
            VStack(spacing: 16) {
                if playback.loading && playback.songs.isEmpty {
                    ProgressView("正在打开点歌…")
                        .frame(maxHeight: .infinity)
                } else {
                    VStack(spacing: 8) {
                        Image(systemName: "music.note")
                            .font(.system(size: 44))
                            .foregroundStyle(.green)
                        Text(playback.current?.label ?? "点歌")
                            .font(.headline)
                            .multilineTextAlignment(.center)
                            .padding(.horizontal)
                        if !playback.status.isEmpty {
                            Text(playback.status)
                                .font(.footnote)
                                .foregroundStyle(.red)
                                .multilineTextAlignment(.center)
                                .padding(.horizontal)
                        }
                        HStack(spacing: 28) {
                            Button { playback.prev() } label: {
                                Image(systemName: "backward.fill").font(.title)
                            }
                            Button { playback.toggle() } label: {
                                Image(systemName: playback.playing ? "pause.circle.fill" : "play.circle.fill")
                                    .font(.system(size: 56))
                            }
                            Button { playback.next() } label: {
                                Image(systemName: "forward.fill").font(.title)
                            }
                        }
                        .disabled(playback.songs.isEmpty)
                        Button {
                            playback.setPanelOpen(false)
                            onMinimize()
                        } label: {
                            Label("最小化，继续播放", systemImage: "chevron.down")
                                .font(.body.weight(.semibold))
                                .frame(maxWidth: .infinity)
                                .padding(.vertical, 10)
                        }
                        .buttonStyle(.borderedProminent)
                        .padding(.horizontal, 24)
                    }
                    .padding(.top, 12)

                    List {
                        ForEach(Array(playback.songs.enumerated()), id: \.element.id) { i, song in
                            Button {
                                playback.playAt(i)
                            } label: {
                                HStack {
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(song.name)
                                            .foregroundStyle(.primary)
                                        Text(song.origin.isEmpty ? song.size : "\(song.size) @\(song.origin)")
                                            .font(.caption)
                                            .foregroundStyle(.secondary)
                                    }
                                    Spacer()
                                    if i == playback.index {
                                        Image(systemName: "speaker.wave.2.fill")
                                            .foregroundStyle(.green)
                                    }
                                }
                            }
                        }
                    }
                    .listStyle(.plain)
                }
            }
            .navigationTitle("点歌")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("最小化") {
                        playback.setPanelOpen(false)
                        onMinimize()
                    }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("关闭") {
                        playback.close()
                        onClose()
                    }
                }
            }
        }
        .onAppear {
            playback.onWebFallback = onWebFallback
            playback.open(pageURL: pageURL, key: key)
        }
    }
}

struct MusicMiniBar: View {
    var onOpen: () -> Void
    @ObservedObject private var playback = MusicPlayback.shared

    var body: some View {
        if playback.hasSession, !playback.panelOpen {
            HStack(spacing: 10) {
                Button(action: onOpen) {
                    HStack(spacing: 10) {
                        Image(systemName: "music.note")
                        Text(playback.current?.label ?? "点歌")
                            .lineLimit(1)
                        Spacer(minLength: 0)
                    }
                }
                .foregroundStyle(.primary)
                Button { playback.toggle() } label: {
                    Image(systemName: playback.playing ? "pause.fill" : "play.fill")
                }
                Button {
                    playback.close()
                } label: {
                    Image(systemName: "xmark")
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12))
            .padding(.horizontal, 12)
            .padding(.bottom, 8)
        }
    }
}
