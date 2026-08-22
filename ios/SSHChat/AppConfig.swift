import Foundation

enum AppConfig {
    static let defaultHost = "stdlib.gicp.net"
    static let defaultPort = 44681

    private static let hostKey = "sshchat.host"
    private static let portKey = "sshchat.port"

    static var sshHost: String {
        let saved = UserDefaults.standard.string(forKey: hostKey)?.trimmingCharacters(in: .whitespacesAndNewlines)
        return (saved?.isEmpty == false) ? saved! : defaultHost
    }

    static var sshPort: Int {
        let saved = UserDefaults.standard.integer(forKey: portKey)
        return (1...65535).contains(saved) ? saved : defaultPort
    }

    static var displayHost: String { "\(sshHost):\(sshPort)" }

    static func saveServer(host: String, port: Int) {
        UserDefaults.standard.set(host.trimmingCharacters(in: .whitespacesAndNewlines), forKey: hostKey)
        UserDefaults.standard.set(port, forKey: portKey)
    }

    static func parsePort(_ text: String) -> Int? {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let n = Int(t), (1...65535).contains(n) else { return nil }
        return n
    }
}
