import Foundation

enum SecureUpload {
    static func upload(url: String, key: String, fileURL: URL) async throws -> String {
        let filename = fileURL.lastPathComponent
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "\\", with: "_")
        let safeName = String(filename.prefix(200)).isEmpty ? "file.bin" : String(filename.prefix(200))
        var mime = MediaMime.guess(safeName)
        if mime == "application/octet-stream" { mime = "application/octet-stream" }
        let data = try Data(contentsOf: fileURL)
        let boundary = "----SSHChat\(UUID().uuidString.replacingOccurrences(of: "-", with: ""))"
        var body = Data()
        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        body.append("Content-Disposition: form-data; name=\"file\"; filename=\"\(safeName)\"\r\n".data(using: .utf8)!)
        body.append("Content-Type: \(mime)\r\n\r\n".data(using: .utf8)!)
        body.append(data)
        body.append("\r\n--\(boundary)--\r\n".data(using: .utf8)!)

        var req = URLRequest(url: URL(string: url)!)
        req.httpMethod = "POST"
        req.timeoutInterval = 180
        req.setValue(key.uppercased(), forHTTPHeaderField: "X-Upload-Key")
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        req.httpBody = body

        let (respData, resp) = try await URLSession.shared.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        let raw = String(data: respData, encoding: .utf8) ?? ""
        let json = (try? JSONSerialization.jsonObject(with: respData) as? [String: Any]) ?? [:]
        if !(200...299).contains(code) {
            let err = (json["error"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines)
            throw UploadError.message(err?.isEmpty == false ? err! : (raw.prefix(200).isEmpty ? "HTTP \(code)" : String(raw.prefix(200))))
        }
        if let err = json["error"] as? String, !err.isEmpty {
            throw UploadError.message(err)
        }
        if let remote = json["filename"] as? String, !remote.isEmpty {
            return remote
        }
        return safeName
    }

    enum UploadError: LocalizedError {
        case message(String)
        var errorDescription: String? {
            switch self {
            case .message(let s): return s
            }
        }
    }
}
