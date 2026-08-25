import Foundation

enum SecureInvite {
    enum Kind { case download, canvas, upload }

    struct Open {
        let kind: Kind
        let url: String
        let key: String
    }

    struct FileMeta {
        var sender: String?
        var filename: String?
        var room: String?

        mutating func reset() {
            sender = nil
            filename = nil
            room = nil
        }
    }

    private static let guiOpen = try! NSRegularExpression(
        pattern: #"^(?:\[[*]\]\s*)?gui-open\s+(download|canvas|upload)\s+(https?://\S+)\s+([A-Z0-9]{6})\s*$"#,
        options: [.caseInsensitive]
    )

    static func absorbFileMeta(_ line: String, into meta: inout FileMeta) {
        let t = normalize(line)
        if t.hasPrefix("共享画布") || t.hasPrefix("文件上传信息") || t.hasPrefix("收到新文件")
            || t.lowercased().hasPrefix("shared canvas") || t.lowercased().hasPrefix("file upload")
            || t.lowercased().hasPrefix("new file")
        {
            meta.reset()
            return
        }
        let patterns: [(String, WritableKeyPath<FileMeta, String?>)] = [
            (#"^(?:发起人|发件人|From|Sender)\s*[:：]\s*(.+)$"#, \.sender),
            (#"^(?:文件名|Filename)\s*[:：]\s*(.+)$"#, \.filename),
            (#"^(?:范围|来自房间|Room)\s*[:：]\s*(.+)$"#, \.room),
        ]
        for (pattern, keyPath) in patterns {
            guard let re = try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive]),
                  let m = re.firstMatch(in: t, range: NSRange(t.startIndex..., in: t)),
                  let r = Range(m.range(at: 1), in: t)
            else { continue }
            meta[keyPath: keyPath] = String(t[r]).trimmingCharacters(in: .whitespaces)
            return
        }
    }

    static func parseGuiOpen(_ line: String) -> Open? {
        let t = normalize(line)
        let range = NSRange(t.startIndex..., in: t)
        guard let m = guiOpen.firstMatch(in: t, range: range), m.numberOfRanges == 4,
              let kindR = Range(m.range(at: 1), in: t),
              let urlR = Range(m.range(at: 2), in: t),
              let keyR = Range(m.range(at: 3), in: t)
        else { return nil }
        let kind: Kind
        switch t[kindR].lowercased() {
        case "download": kind = .download
        case "canvas": kind = .canvas
        case "upload": kind = .upload
        default: return nil
        }
        return Open(kind: kind, url: String(t[urlR]), key: String(t[keyR]).uppercased())
    }

    static func isInviteNoise(_ line: String) -> Bool {
        let t = normalize(line)
        if t.isEmpty { return true }
        if parseGuiOpen(t) != nil { return true }
        let lower = t.lowercased()
        if t.hasPrefix("===") { return true }
        if t.hasPrefix("共享画布") || t.hasPrefix("文件上传信息") || t.hasPrefix("收到新文件") { return true }
        if lower.hasPrefix("shared canvas") || lower.hasPrefix("file upload") || lower.hasPrefix("new file") { return true }
        if t.range(of: #"^=+\s*$"#, options: .regularExpression) != nil { return true }
        if t.range(of: #"(画布网址|上传网址|下载网址|Canvas\s*URL|Upload\s*URL|Download\s*URL|网址)\s*:?\s*$"#, options: [.regularExpression, .caseInsensitive]) != nil {
            return true
        }
        if t.range(of: #"^(?:访问密钥|上传密钥|下载密钥|Access\s*key|Upload\s*key|Download\s*key|密钥)\s*[:：]\s*[A-Z0-9]{6}\s*$"#, options: [.regularExpression, .caseInsensitive]) != nil {
            return true
        }
        if t.range(of: #"^https?://\S+$"#, options: [.regularExpression, .caseInsensitive]) != nil { return true }
        if t.range(of: #"^(说明|Instructions?)\s*:?\s*$"#, options: [.regularExpression, .caseInsensitive]) != nil { return true }
        if t.range(of: #"^\d+\.\s+(打开|选择|输入|上传|下载|文件只能|每个接收|图形客户端|Enter|Open|Click|Choose|Select|Upload|Download|Preview|This page|Each recipient|The key|Verify)"#, options: [.regularExpression, .caseInsensitive]) != nil {
            return true
        }
        if t.range(of: #"^(发起人|发件人|文件名|大小|范围|来自房间|标题|接收者|房间|发送者|From|Sender|Filename|Size|Room|Recipients?)\s*[:：]"#, options: [.regularExpression, .caseInsensitive]) != nil {
            return true
        }
        if t.hasPrefix("经联邦节点") { return true }
        if t.contains("图形客户端会折叠") { return true }
        if t.contains("只能下载一次") || t.contains("存好之前别关") { return true }
        if t.contains("网址和密钥都不同") { return true }
        if t.contains("此网址随后作废") { return true }
        return false
    }

    /** Server rejected /sendfile before issuing gui-open upload. */
    static func isSendfileFailure(_ line: String) -> Bool {
        let t = normalize(line)
        if t.isEmpty { return false }
        return t.range(
            of: #"(没有其他用户|no other users|文件传输功能未启用|创建文件传输失败|File transfer is disabled|无效的房间名|你不在房间|房间\s+#\S+\s+不存在)"#,
            options: [.regularExpression, .caseInsensitive]
        ) != nil
    }

    private static func normalize(_ raw: String) -> String {
        var s = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if let re = try? NSRegularExpression(pattern: #"^(?:\[[\d:.\sAPMapm/-]+]\s*)+"#) {
            s = re.stringByReplacingMatches(in: s, range: NSRange(s.startIndex..., in: s), withTemplate: "")
                .trimmingCharacters(in: .whitespaces)
        }
        if let re = try? NSRegularExpression(pattern: #"^\[\*]\s*"#) {
            s = re.stringByReplacingMatches(in: s, range: NSRange(s.startIndex..., in: s), withTemplate: "")
                .trimmingCharacters(in: .whitespaces)
        }
        return s
    }
}
