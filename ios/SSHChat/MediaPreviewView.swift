import AVFoundation
import AVKit
import SwiftUI

struct MediaPreviewView: View {
    let media: DownloadedMedia
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Group {
                if media.isImage {
                    ZoomableImage(url: media.url)
                } else if media.isVideo {
                    VideoPlayer(player: AVPlayer(url: media.url))
                } else if media.isAudio {
                    AudioPreview(url: media.url)
                } else {
                    ContentUnavailableView("可分享到其他应用打开", systemImage: "doc")
                }
            }
            .navigationTitle(media.name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("关闭") { dismiss() }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    ShareLink(item: media.url) {
                        Image(systemName: "square.and.arrow.up")
                    }
                }
            }
        }
    }
}

private struct ZoomableImage: View {
    let url: URL
    @State private var scale: CGFloat = 1
    @State private var offset: CGSize = .zero

    var body: some View {
        GeometryReader { geo in
            if let ui = UIImage(contentsOfFile: url.path) {
                Image(uiImage: ui)
                    .resizable()
                    .scaledToFit()
                    .scaleEffect(scale)
                    .offset(offset)
                    .frame(width: geo.size.width, height: geo.size.height)
                    .gesture(MagnificationGesture().onChanged { scale = max(0.5, min(6, $0)) })
                    .simultaneousGesture(DragGesture().onChanged { offset = $0.translation })
            }
        }
    }
}

private struct AudioPreview: View {
    let url: URL
    @State private var player: AVAudioPlayer?
    @State private var playing = false

    var body: some View {
        VStack(spacing: 24) {
            Image(systemName: "waveform")
                .font(.system(size: 64))
                .foregroundStyle(.green)
            Text("语音消息")
            Button(playing ? "暂停" : "播放") { toggle() }
                .buttonStyle(.borderedProminent)
        }
        .onAppear { toggle() }
        .onDisappear {
            player?.stop()
            player = nil
        }
    }

    private func toggle() {
        if let player, player.isPlaying {
            player.pause()
            playing = false
            return
        }
        if let player {
            player.play()
            playing = true
            return
        }
        do {
            try AVAudioSession.sharedInstance().setCategory(.playback)
            try AVAudioSession.sharedInstance().setActive(true)
            let p = try AVAudioPlayer(contentsOf: url)
            p.play()
            player = p
            playing = true
        } catch {
            playing = false
        }
    }
}
