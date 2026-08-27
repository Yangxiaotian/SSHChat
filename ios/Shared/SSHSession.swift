import Citadel
import Crypto
import Foundation
import NIO
@preconcurrency import NIOSSH

/// Interactive SSH shell for SSHChat (ForceCommand / login shell).
actor SSHSession {
    private var client: SSHClient?
    private var outbound: TTYStdinWriter?
    private var readerTask: Task<Void, Never>?

    func connect(
        host: String,
        port: Int,
        username: String,
        privateSeed: Data,
        onLine: @escaping @MainActor @Sendable (_ line: String, _ serverBell: Bool) -> Void,
        onStatus: @escaping @Sendable (String) -> Void,
        onDisconnect: @escaping @Sendable (String?) -> Void
    ) async throws {
        await disconnect()
        onStatus("连接中 \(host):\(port) …")

        let pk = try Curve25519.Signing.PrivateKey(rawRepresentation: privateSeed)
        let auth = SSHAuthenticationMethod.ed25519(username: username, privateKey: pk)

        let client = try await SSHClient.connect(
            host: host,
            port: port,
            authenticationMethod: auth,
            hostKeyValidator: .acceptAnything(), // ponytail: tryout only; pin host keys later
            reconnect: .never
        )
        self.client = client

        let pty = SSHChannelRequestEvent.PseudoTerminalRequest(
            wantReply: true,
            term: "xterm",
            terminalCharacterWidth: 160,
            terminalRowHeight: 48,
            terminalPixelWidth: 0,
            terminalPixelHeight: 0,
            terminalModes: .init([:])
        )

        let ready = CheckedContinuationBox<Void>()
        readerTask = Task { [weak self] in
            do {
                try await client.withPTY(pty) { inbound, outbound in
                    await self?.storeOutbound(outbound)
                    await ready.resumeOnce(())
                    // Decode UTF-8 across chunk boundaries (per-chunk String(buffer:)
                    // turns a split 房 into two replacement chars / ??).
                    var lines = Utf8LineBuffer()
                    for try await chunk in inbound {
                        switch chunk {
                        case .stdout(let bytes), .stderr(let bytes):
                            for line in lines.append(bytes) {
                                // Await MainActor so /lib show lines stay in order (unstructured
                                // Task { @MainActor } can reorder and orphan a [*] system row).
                                await Self.emitLine(line, onLine: onLine)
                            }
                        }
                    }
                    if let rest = lines.finish() {
                        await Self.emitLine(rest, onLine: onLine)
                    }
                }
                onDisconnect(nil)
            } catch is CancellationError {
                await ready.resumeErrorOnce(CancellationError())
                onDisconnect(nil)
            } catch {
                await ready.resumeErrorOnce(error)
                onDisconnect(error.localizedDescription)
            }
        }

        try await ready.wait()
        onStatus("已连接 \(host):\(port)")
    }

    func send(_ text: String) async throws {
        guard let outbound else {
            throw SSHSessionError.notConnected
        }
        var payload = text
        if !payload.hasSuffix("\n") {
            payload += "\n"
        }
        try await outbound.write(ByteBuffer(string: payload))
    }

    func disconnect() async {
        readerTask?.cancel()
        readerTask = nil
        outbound = nil
        if let client {
            try? await client.close()
        }
        self.client = nil
    }

    private func storeOutbound(_ writer: TTYStdinWriter) {
        outbound = writer
    }

    private static func emitLine(
        _ raw: String,
        onLine: @escaping @MainActor @Sendable (_ line: String, _ serverBell: Bool) -> Void
    ) async {
        // OSC / title sequences are BEL-terminated (`ESC ] … BEL`). Strip those first so
        // their BEL is not mistaken for client.py's peer-chat alert bell.
        let withoutOSC = stripOSC(raw)
        let serverBell = withoutOSC.contains("\u{0007}")
        let cleaned = cleanLine(withoutOSC)
        guard hasVisibleContent(cleaned) else { return }
        await MainActor.run {
            onLine(cleaned, serverBell)
        }
    }

    /// Remove ESC] … BEL (and ESC] … ST) operating-system commands.
    private static func stripOSC(_ raw: String) -> String {
        var s = raw
        if let re = try? NSRegularExpression(pattern: #"\u001B\][^\u0007\u001B]*(?:\u0007|\u001B\\)"#) {
            s = re.stringByReplacingMatches(in: s, range: NSRange(s.startIndex..., in: s), withTemplate: "")
        }
        return s
    }

    private static func hasVisibleContent(_ s: String) -> Bool {
        // Need a real chat/system marker or a letter/digit/CJK — not a lone "?" from mangled CSI.
        if s.contains("["), s.contains("]") { return true }
        for ch in s where !ch.isWhitespace && !ch.isNewline {
            if ch.isLetter || ch.isNumber { return true }
            if ch.unicodeScalars.contains(where: { $0.value > 0x7F }) { return true }
        }
        return false
    }

    static func cleanLine(_ raw: String) -> String {
        var s = raw.replacingOccurrences(of: "\r", with: "")
        s = s.replacingOccurrences(of: "\u{0007}", with: "") // bell
        if let re = try? NSRegularExpression(pattern: #"\u001B\[[0-9;?]*[ -/]*[@-~]"#) {
            let range = NSRange(s.startIndex..., in: s)
            s = re.stringByReplacingMatches(in: s, range: range, withTemplate: "")
        }
        if let re = try? NSRegularExpression(pattern: #"\u001B\][^\u0007]*\u0007"#) {
            let range = NSRange(s.startIndex..., in: s)
            s = re.stringByReplacingMatches(in: s, range: range, withTemplate: "")
        }
        // prompt_toolkit sometimes mangles ESC to '?'
        if let re = try? NSRegularExpression(pattern: #"\?\[([0-9;?]*)([A-Za-z@-~])"#) {
            let range = NSRange(s.startIndex..., in: s)
            s = re.stringByReplacingMatches(in: s, range: range, withTemplate: "")
        }
        s = s.replacingOccurrences(of: "\u{001B}", with: "")
        // Keep leading spaces — board padding / 楚河汉界 centering depends on them.
        while let last = s.last, last == "\n" || last == "\r" || last == " " || last == "\t" {
            s.removeLast()
        }
        while let first = s.first, first == "\n" || first == "\r" {
            s.removeFirst()
        }
        return s
    }
}

enum SSHSessionError: LocalizedError {
    case notConnected

    var errorDescription: String? {
        switch self {
        case .notConnected: return "未连接"
        }
    }
}

private actor CheckedContinuationBox<T: Sendable> {
    private var cont: CheckedContinuation<T, Error>?
    private var pending: Result<T, Error>?

    func wait() async throws -> T {
        if let pending {
            self.pending = nil
            return try pending.get()
        }
        return try await withCheckedThrowingContinuation { (c: CheckedContinuation<T, Error>) in
            if let pending = self.pending {
                self.pending = nil
                c.resume(with: pending)
            } else {
                self.cont = c
            }
        }
    }

    func resumeOnce(_ value: T) {
        if let cont {
            self.cont = nil
            cont.resume(returning: value)
        } else if pending == nil {
            pending = .success(value)
        }
    }

    func resumeErrorOnce(_ error: Error) {
        if let cont {
            self.cont = nil
            cont.resume(throwing: error)
        } else if pending == nil {
            pending = .failure(error)
        }
    }
}
