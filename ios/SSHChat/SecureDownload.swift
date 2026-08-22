import Foundation

enum SecureDownload {
    static func fetch(pageURL: String, key: String, outDir: URL) async throws -> DownloadedMedia {
        let (base, token) = try splitDownloadURL(pageURL)
        try FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)

        let ticketURL = URL(string: "\(base)/download/\(token)/ticket")!
        var ticketReq = URLRequest(url: ticketURL)
        ticketReq.httpMethod = "POST"
        ticketReq.timeoutInterval = 180
        ticketReq.setValue("application/json", forHTTPHeaderField: "Content-Type")
        ticketReq.httpBody = try JSONSerialization.data(withJSONObject: ["key": key.uppercased()])

        let (ticketData, ticketResp) = try await URLSession.shared.data(for: ticketReq)
        try throwIfBad(ticketResp, ticketData)
        let ticket = (try? JSONSerialization.jsonObject(with: ticketData) as? [String: Any]) ?? [:]
        let filename = (ticket["filename"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines)
        let name = (filename?.isEmpty == false) ? filename! : "file"
        var mime = (ticket["mime"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if mime.isEmpty { mime = MediaMime.guess(name) }
        guard let rel = ticket["download"] as? String, !rel.isEmpty else {
            throw DownloadError.message("no download ticket")
        }
        let fileURL = URL(string: rel.hasPrefix("/") ? "\(base)\(rel)" : "\(base)/\(rel)")!
        let (bytes, fileResp) = try await URLSession.shared.data(from: fileURL)
        try throwIfBad(fileResp, bytes)

        let safe = name
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "\\", with: "_")
        let clipped = String(safe.prefix(180)).isEmpty ? "file" : String(safe.prefix(180))
        let out = outDir.appendingPathComponent("\(Int(Date().timeIntervalSince1970 * 1000))-\(clipped)")
        try bytes.write(to: out)
        return DownloadedMedia(url: out, name: name, mime: mime)
    }

    private static func splitDownloadURL(_ url: String) throws -> (String, String) {
        guard let u = URL(string: url) else { throw DownloadError.message("invalid download url") }
        let parts = u.path.split(separator: "/").map(String.init).filter { !$0.isEmpty }
        guard parts.count >= 2, parts[0] == "download" else {
            throw DownloadError.message("invalid download url")
        }
        let base = "\(u.scheme ?? "https")://\(u.host ?? "")\(u.port.map { ":\($0)" } ?? "")"
        return (base, parts[1])
    }

    private static func throwIfBad(_ resp: URLResponse, _ data: Data) throws {
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if (200...299).contains(code) { return }
        let raw = String(data: data, encoding: .utf8) ?? ""
        let json = (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
        let err = (json["error"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines)
        throw DownloadError.message(err?.isEmpty == false ? err! : (raw.prefix(200).isEmpty ? "HTTP \(code)" : String(raw.prefix(200))))
    }

    enum DownloadError: LocalizedError {
        case message(String)
        var errorDescription: String? {
            switch self {
            case .message(let s): return s
            }
        }
    }
}
