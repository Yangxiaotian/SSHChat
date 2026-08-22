import CryptoKit
import Foundation
import Security
import UIKit

/// Ed25519 identity with Keychain + Documents backup (same file format as Android).
enum DeviceKeyStore {
    static let durableName = "SSHChat-ed25519.identity"
    private static let keychainService = "com.qq.267267275.SSHChat.identity"
    private static let keychainAccount = "ed25519"

    struct Keys {
        let privateSeed: Data
        let publicOpenSshLine: String
        let comment: String
        let freshlyGenerated: Bool
        let restoredFromBackup: Bool
    }

    static func getOrCreate() -> Keys {
        if let local = loadKeychain() {
            persistAll(seed: local.privateSeed, pubLine: local.publicOpenSshLine, comment: local.comment)
            return local
        }
        if let file = readDurable() {
            return installRestored(file)
        }
        return createNew()
    }

    static func importFromData(_ raw: Data) throws -> Keys {
        guard let parsed = parseBody(String(data: raw, encoding: .utf8) ?? "") else {
            throw KeyStoreError.invalidBackup
        }
        return installRestored(parsed)
    }

    static func createNew() -> Keys {
        let priv = Curve25519.Signing.PrivateKey()
        let seed = priv.rawRepresentation
        let model = UIDevice.current.model.replacingOccurrences(of: " ", with: "-")
        let comment = "sshchat-ios@\(model)"
        let pubLine = formatOpenSshPublicKey(priv.publicKey, comment: comment)
        persistAll(seed: seed, pubLine: pubLine, comment: comment)
        return Keys(
            privateSeed: seed,
            publicOpenSshLine: pubLine,
            comment: comment,
            freshlyGenerated: true,
            restoredFromBackup: false
        )
    }

    static func signingKey(from seed: Data) throws -> Curve25519.Signing.PrivateKey {
        try Curve25519.Signing.PrivateKey(rawRepresentation: seed)
    }

    private static func installRestored(_ keys: Keys) -> Keys {
        persistAll(seed: keys.privateSeed, pubLine: keys.publicOpenSshLine, comment: keys.comment)
        return Keys(
            privateSeed: keys.privateSeed,
            publicOpenSshLine: keys.publicOpenSshLine,
            comment: keys.comment,
            freshlyGenerated: false,
            restoredFromBackup: true
        )
    }

    private static func persistAll(seed: Data, pubLine: String, comment: String) {
        saveKeychain(seed: seed, pubLine: pubLine, comment: comment)
        writeDurable(seed: seed, pubLine: pubLine, comment: comment)
    }

    private static func encodeBody(seed: Data, pubLine: String, comment: String) -> String {
        "v1\n\(seed.base64EncodedString())\n\(pubLine)\n\(comment)\n"
    }

    private static func parseBody(_ raw: String) -> Keys? {
        let lines = raw
            .split(whereSeparator: \.isNewline)
            .map { String($0).trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
        guard lines.count >= 4, lines[0] == "v1" else { return nil }
        guard let seed = Data(base64Encoded: lines[1]), seed.count == 32 else { return nil }
        let pub = lines[2]
        let comment = lines[3]
        guard pub.hasPrefix("ssh-ed25519 ") else { return nil }
        return Keys(
            privateSeed: seed,
            publicOpenSshLine: pub,
            comment: comment,
            freshlyGenerated: false,
            restoredFromBackup: true
        )
    }

    private static func durableURL() -> URL {
        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        let dir = docs.appendingPathComponent("SSHChat", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent(durableName)
    }

    private static func writeDurable(seed: Data, pubLine: String, comment: String) {
        let body = encodeBody(seed: seed, pubLine: pubLine, comment: comment)
        try? body.write(to: durableURL(), atomically: true, encoding: .utf8)
    }

    private static func readDurable() -> Keys? {
        guard let raw = try? String(contentsOf: durableURL(), encoding: .utf8) else { return nil }
        return parseBody(raw)
    }

    private static func saveKeychain(seed: Data, pubLine: String, comment: String) {
        let payload: [String: String] = [
            "seed": seed.base64EncodedString(),
            "pub": pubLine,
            "comment": comment,
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: payload) else { return }
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: keychainService,
            kSecAttrAccount as String: keychainAccount,
        ]
        SecItemDelete(query as CFDictionary)
        var add = query
        add[kSecValueData as String] = data
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        SecItemAdd(add as CFDictionary, nil)
    }

    private static func loadKeychain() -> Keys? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: keychainService,
            kSecAttrAccount as String: keychainAccount,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data,
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: String],
              let seedB64 = obj["seed"],
              let pub = obj["pub"],
              let comment = obj["comment"],
              let seed = Data(base64Encoded: seedB64),
              seed.count == 32
        else { return nil }
        return Keys(
            privateSeed: seed,
            publicOpenSshLine: pub,
            comment: comment,
            freshlyGenerated: false,
            restoredFromBackup: false
        )
    }

    private static func formatOpenSshPublicKey(_ pub: Curve25519.Signing.PublicKey, comment: String) -> String {
        let keyBytes = pub.rawRepresentation
        let type = Data("ssh-ed25519".utf8)
        var blob = Data()
        func writeString(_ d: Data) {
            var len = UInt32(d.count).bigEndian
            blob.append(Data(bytes: &len, count: 4))
            blob.append(d)
        }
        writeString(type)
        writeString(keyBytes)
        return "ssh-ed25519 \(blob.base64EncodedString()) \(comment)"
    }

    enum KeyStoreError: LocalizedError {
        case invalidBackup
        var errorDescription: String? { "不是有效的 SSHChat 身份备份" }
    }
}
