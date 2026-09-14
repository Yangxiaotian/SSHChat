import AVFoundation
import Foundation
import UIKit

/// Short receive chime for peer chat / PM.
/// Uses `.ambient` so the hardware mute switch is respected.
enum MessageAlert {
    private static var lastPlayAt: TimeInterval = 0
    private static let minInterval: TimeInterval = 0.45
    private static var player: AVAudioPlayer?
    // Intentionally not cached across tone tweaks — rebuild is cheap (~3KB).

    /// Exact body of the last outbound chat line (for echo suppression only).
    private static var recentOutboundBody: String = ""
    private static var recentOutboundAt: TimeInterval = 0

    /// Call right before / after sending so our own echo does not chime.
    static func noteOutbound(body: String) {
        let b = body.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !b.isEmpty else { return }
        recentOutboundBody = b
        recentOutboundAt = Date().timeIntervalSince1970
    }

    static func playIfNeeded(for line: String, myName: String, serverBell: Bool = false) {
        // Only chime when the line is real peer chat / PM / join-leave.
        // Do NOT play on serverBell alone — OSC title sequences also end with BEL and
        // were causing a second chime (and sometimes a blank row) before the chat line.
        _ = serverBell
        guard ChatLineParsers.shouldAlert(
            line,
            myName: myName,
            recentOutboundBody: recentOutboundBody,
            recentOutboundAt: recentOutboundAt
        ) else { return }
        play()
    }

    static func play() {
        let work = {
            let now = Date().timeIntervalSince1970
            guard now - lastPlayAt >= minInterval else { return }
            lastPlayAt = now

            // .ambient → silent when the mute switch is on (user request).
            // Do NOT use .playback (ignores mute) or bare SystemSound IDs
            // (1007 often no-ops on modern iOS).
            do {
                let session = AVAudioSession.sharedInstance()
                try session.setCategory(.ambient, mode: .default, options: [.mixWithOthers])
                try session.setActive(true, options: [])
            } catch {}

            _ = playWavBeep()

            let gen = UINotificationFeedbackGenerator()
            gen.prepare()
            gen.notificationOccurred(.success)
        }

        if Thread.isMainThread {
            work()
        } else {
            DispatchQueue.main.async(execute: work)
        }
    }

    @discardableResult
    private static func playWavBeep() -> Bool {
        guard let data = buildBeepWav() else { return false }
        do {
            let p = try AVAudioPlayer(data: data)
            p.volume = 1.0
            guard p.prepareToPlay() else { return false }
            player = p
            return p.play()
        } catch {
            return false
        }
    }

    private static func buildBeepWav() -> Data? {
        // 16-bit mono PCM WAV, 22.05 kHz, ~160ms single tone (one chime, not two).
        let sampleRate = 22_050
        let duration = 0.16
        let frequency = 880.0
        let n = Int(Double(sampleRate) * duration)
        var data = Data()
        data.reserveCapacity(44 + n * 2)

        func appendU32(_ v: UInt32) {
            var le = v.littleEndian
            withUnsafeBytes(of: &le) { data.append(contentsOf: $0) }
        }
        func appendU16(_ v: UInt16) {
            var le = v.littleEndian
            withUnsafeBytes(of: &le) { data.append(contentsOf: $0) }
        }

        let dataSize = UInt32(n * 2)
        data.append(contentsOf: Array("RIFF".utf8))
        appendU32(36 + dataSize)
        data.append(contentsOf: Array("WAVEfmt ".utf8))
        appendU32(16)
        appendU16(1)
        appendU16(1)
        appendU32(UInt32(sampleRate))
        appendU32(UInt32(sampleRate * 2))
        appendU16(2)
        appendU16(16)
        data.append(contentsOf: Array("data".utf8))
        appendU32(dataSize)

        for i in 0..<n {
            let t = Double(i) / Double(sampleRate)
            let env: Double
            if t < 0.008 { env = t / 0.008 }
            else if t > duration - 0.03 { env = max(0, (duration - t) / 0.03) }
            else { env = 1 }
            let sample = sin(2 * Double.pi * frequency * t) * env * 0.65
            let clipped = max(-1.0, min(1.0, sample))
            let s = Int16((clipped * Double(Int16.max)).rounded())
            var le = s.littleEndian
            withUnsafeBytes(of: &le) { data.append(contentsOf: $0) }
        }
        return data
    }
}
