import AVFoundation
import Foundation
import SwiftUI
import UIKit

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
        try session.setCategory(
            .playAndRecord,
            mode: .spokenAudio,
            options: [.defaultToSpeaker, .allowBluetooth, .allowBluetoothA2DP]
        )
        try session.setActive(true, options: [])

        let settings: [String: Any] = [
            AVFormatIDKey: Int(kAudioFormatMPEG4AAC),
            AVSampleRateKey: 44100,
            AVNumberOfChannelsKey: 1,
            AVEncoderAudioQualityKey: AVAudioQuality.medium.rawValue,
            AVEncoderBitRateKey: 64000,
        ]
        let rec = try AVAudioRecorder(url: file, settings: settings)
        rec.isMeteringEnabled = false
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
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
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
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        if let file {
            try? FileManager.default.removeItem(at: file)
        }
    }

    enum RecorderError: LocalizedError {
        case failed
        var errorDescription: String? { "无法录音" }
    }
}

enum VoicePermission {
    static func request(_ completion: @escaping (Bool) -> Void) {
        if #available(iOS 17.0, *) {
            AVAudioApplication.requestRecordPermission { granted in
                DispatchQueue.main.async { completion(granted) }
            }
        } else {
            AVAudioSession.sharedInstance().requestRecordPermission { granted in
                DispatchQueue.main.async { completion(granted) }
            }
        }
    }

    static func status() -> AVAudioSession.RecordPermission {
        AVAudioSession.sharedInstance().recordPermission
    }
}

/// UIKit push-to-talk — SwiftUI Button + DragGesture misses touchDown on iOS.
struct PushToTalkButton: UIViewRepresentable {
    let active: Bool
    let enabled: Bool
    let onPressBegan: () -> Void
    let onPressEnded: (Bool) -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(onPressBegan: onPressBegan, onPressEnded: onPressEnded)
    }

    func makeUIView(context: Context) -> UIButton {
        let button = UIButton(type: .system)
        button.clipsToBounds = true
        button.contentVerticalAlignment = .center
        button.contentHorizontalAlignment = .center
        button.setContentHuggingPriority(.required, for: .vertical)
        button.setContentCompressionResistancePriority(.required, for: .vertical)
        button.addTarget(context.coordinator, action: #selector(Coordinator.touchDown), for: .touchDown)
        button.addTarget(context.coordinator, action: #selector(Coordinator.touchUpInside), for: .touchUpInside)
        button.addTarget(context.coordinator, action: #selector(Coordinator.touchUpOutside), for: .touchUpOutside)
        button.addTarget(context.coordinator, action: #selector(Coordinator.touchCancel), for: .touchCancel)
        update(button)
        return button
    }

    func updateUIView(_ uiView: UIButton, context: Context) {
        context.coordinator.onPressBegan = onPressBegan
        context.coordinator.onPressEnded = onPressEnded
        update(uiView)
    }

    func sizeThatFits(_ proposal: ProposedViewSize, uiView: UIButton, context: Context) -> CGSize? {
        CGSize(width: proposal.width ?? 44, height: 44)
    }

    private func update(_ button: UIButton) {
        var config = UIButton.Configuration.plain()
        config.image = UIImage(systemName: "mic.fill", withConfiguration: UIImage.SymbolConfiguration(pointSize: 18, weight: .regular))
        config.baseForegroundColor = active ? .systemRed : UIColor(white: 0.18, alpha: 1)
        config.background.backgroundColor = UIColor(white: 0.97, alpha: 1)
        config.background.cornerRadius = 8
        config.contentInsets = NSDirectionalEdgeInsets(top: 8, leading: 8, bottom: 8, trailing: 8)
        button.configuration = config
        button.isEnabled = enabled || active
        button.alpha = (enabled || active) ? 1 : 0.35
    }

    final class Coordinator: NSObject {
        var onPressBegan: () -> Void
        var onPressEnded: (Bool) -> Void

        init(onPressBegan: @escaping () -> Void, onPressEnded: @escaping (Bool) -> Void) {
            self.onPressBegan = onPressBegan
            self.onPressEnded = onPressEnded
        }

        @objc func touchDown() { onPressBegan() }
        @objc func touchUpInside() { onPressEnded(true) }
        @objc func touchUpOutside() { onPressEnded(true) }
        @objc func touchCancel() { onPressEnded(false) }
    }
}
