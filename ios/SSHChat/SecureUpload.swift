import Foundation

enum SecureUpload {
    private static let chunkSize = 512 * 1024
    private static let attempts = 4

    static func upload(url: String, key: String, fileURL: URL) async throws -> String {
        let filename = fileURL.lastPathComponent
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "\\", with: "_")
        let safeName = String(filename.prefix(200)).isEmpty ? "file.bin" : String(filename.prefix(200))
        let attrs = try FileManager.default.attributesOfItem(atPath: fileURL.path)
        let size = (attrs[.size] as? NSNumber)?.intValue ?? 0
        guard size > 0 else { throw UploadError.message("empty file") }
        let count = max(1, (size + chunkSize - 1) / chunkSize)
        let encoded = safeName.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? safeName
        let handle = try FileHandle(forReadingFrom: fileURL)
        defer { try? handle.close() }
        var remote = safeName
        for index in 0..<count {
            let data = try handle.read(upToCount: chunkSize) ?? Data()
            guard !data.isEmpty else { throw UploadError.message("short file read") }
            var lastErr: Error?
            for attempt in 0..<attempts {
                do {
                    let name = try await postChunk(
                        url: url, key: key, encodedName: encoded,
                        size: size, index: index, count: count, data: data
                    )
                    if !name.isEmpty { remote = name }
                    lastErr = nil
                    break
                } catch {
                    lastErr = error
                    try await Task.sleep(nanoseconds: UInt64(400_000_000 * (attempt + 1)))
                }
            }
            if let lastErr {
                throw lastErr
            }
        }
        return remote
    }

    private static func postChunk(
        url: String, key: String, encodedName: String,
        size: Int, index: Int, count: Int, data: Data
    ) async throws -> String {
        var req = URLRequest(url: URL(string: url)!)
        req.httpMethod = "POST"
        req.timeoutInterval = 90
        req.setValue(key.uppercased(), forHTTPHeaderField: "X-Upload-Key")
        req.setValue(String(index), forHTTPHeaderField: "X-Upload-Index")
        req.setValue(String(count), forHTTPHeaderField: "X-Upload-Count")
        req.setValue(String(size), forHTTPHeaderField: "X-Upload-Size")
        req.setValue(encodedName, forHTTPHeaderField: "X-Upload-Filename")
        req.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
        req.httpBody = data

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
        return (json["filename"] as? String) ?? ""
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
