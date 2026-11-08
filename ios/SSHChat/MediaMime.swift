import Foundation

enum MediaMime {
    static func guess(_ name: String) -> String {
        let n = name.lowercased()
        switch true {
        case n.hasSuffix(".png"): return "image/png"
        case n.hasSuffix(".jpg"), n.hasSuffix(".jpeg"): return "image/jpeg"
        case n.hasSuffix(".gif"): return "image/gif"
        case n.hasSuffix(".webp"): return "image/webp"
        case n.hasSuffix(".bmp"): return "image/bmp"
        case n.hasSuffix(".mp4"): return "video/mp4"
        case n.hasSuffix(".weba"): return "audio/webm"
        case n.hasSuffix(".webm") && isVoiceName(n): return "audio/webm"
        case n.hasSuffix(".webm"): return "video/webm"
        case n.hasSuffix(".3gp"), n.hasSuffix(".3gpp"): return "video/3gpp"
        case n.hasSuffix(".mkv"): return "video/x-matroska"
        case n.hasSuffix(".m4a"): return "audio/mp4"
        case n.hasSuffix(".aac"): return "audio/aac"
        case n.hasSuffix(".mp3"): return "audio/mpeg"
        case n.hasSuffix(".ogg"), n.hasSuffix(".oga"): return "audio/ogg"
        case n.hasSuffix(".wav"): return "audio/wav"
        case n.hasSuffix(".amr"): return "audio/amr"
        default: return "application/octet-stream"
        }
    }

    static func isVoiceName(_ name: String) -> Bool {
        let n = (name as NSString).lastPathComponent.lowercased()
        return n.hasPrefix("voice") || n.contains("voice-")
    }

    static func isImage(_ mime: String) -> Bool { mime.lowercased().hasPrefix("image/") }
    static func isVideo(_ mime: String) -> Bool { mime.lowercased().hasPrefix("video/") }
    static func isAudio(_ mime: String, name: String = "") -> Bool {
        if mime.lowercased().hasPrefix("audio/") { return true }
        let n = (name as NSString).lastPathComponent.lowercased()
        if n.hasSuffix(".m4a") || n.hasSuffix(".aac") || n.hasSuffix(".mp3")
            || n.hasSuffix(".amr") || n.hasSuffix(".wav") || n.hasSuffix(".oga")
            || n.hasSuffix(".weba") {
            return true
        }
        if (n.hasSuffix(".webm") || n.hasSuffix(".ogg")) && isVoiceName(n) { return true }
        if mime.lowercased() == "video/webm" && isVoiceName(n) { return true }
        return false
    }

    static func kindLabel(mime: String, name: String = "") -> String {
        if isImage(mime) { return "图片" }
        if isAudio(mime, name: name) { return "语音" }
        if isVideo(mime) { return "视频" }
        return "文件"
    }
}

struct DownloadedMedia: Identifiable, Equatable {
    let id = UUID()
    let url: URL
    let name: String
    let mime: String
    var sender: String?

    var isImage: Bool { MediaMime.isImage(mime) }
    var isAudio: Bool { MediaMime.isAudio(mime, name: name) }
    var isVideo: Bool { MediaMime.isVideo(mime) && !isAudio }
}
