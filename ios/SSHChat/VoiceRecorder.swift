import AVFoundation
import Foundation

final class VoiceRecorder {
    private var recorder: AVAudioRecorder?
    private var target: URL?
    private var startedAt: Date?

    var isRecording: Bool { recorder != nil }

    func start(outDir: URL) throws -> URL {
        cancel()
        try FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)
        let file = outDir.appendingPathComponent("voice-\(Int(Date().timeIntervalSince1970 * 1000)).m4a")
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.playAndRecord, mode: .default, options: [.defaultToSpeaker])
        try session.setActive(true)

        let settings: [String: Any] = [
            AVFormatIDKey: Int(kAudioFormatMPEG4AAC),
            AVSampleRateKey: 44100,
            AVNumberOfChannelsKey: 1,
            AVEncoderBitRateKey: 64000,
        ]
        let rec = try AVAudioRecorder(url: file, settings: settings)
        rec.prepareToRecord()
        guard rec.record() else {
            try? FileManager.default.removeItem(at: file)
            throw RecorderError.failed
        }
        recorder = rec
        target = file
        startedAt = Date()
        return file
    }

    func stop(minMs: Double = 400) -> URL? {
        let file = target
        let rec = recorder
        recorder = nil
        target = nil
        let elapsed = Date().timeIntervalSince(startedAt ?? Date()) * 1000
        startedAt = nil
        rec?.stop()
        guard let file else { return nil }
        let size = (try? FileManager.default.attributesOfItem(atPath: file.path)[.size] as? NSNumber)?.intValue ?? 0
        if elapsed < minMs || size < 200 {
            try? FileManager.default.removeItem(at: file)
            return nil
        }
        return file
    }

    func cancel() {
        let file = target
        recorder?.stop()
        recorder = nil
        target = nil
        startedAt = nil
        if let file {
            try? FileManager.default.removeItem(at: file)
        }
    }

    enum RecorderError: LocalizedError {
        case failed
        var errorDescription: String? { "无法录音" }
    }
}
