import Foundation

enum PtyNoise {
    private static let timePrefix = try! NSRegularExpression(pattern: #"^\[\d{1,2}:\d{2}(?::\d{2})?]\s*"#)

    /// Drop prompt_toolkit / PTY echo noise (same idea as Android PtyNoise).
    static func shouldDrop(_ raw: String) -> Bool {
        var t = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if t.isEmpty { return true }

        // Real chat/system lines always carry a bracket tag — never drop those.
        // Keeps [#room] / [*] / [user] / [OK] visible even if prompt echo heuristics misfire.
        if t.contains("[#") || t.hasPrefix("[*]") || t.hasPrefix("[OK]") || t.hasPrefix("[ERROR]")
            || t.hasPrefix("[INFO]") || t.hasPrefix("[+]") || t.hasPrefix("[-]") || t.hasPrefix("[!]")
        {
            return false
        }
        // "[12:34] [#room] ..." after optional local time — still chat.
        if t.range(of: #"^\[\d{1,2}:\d{2}"#, options: .regularExpression) != nil {
            return false
        }

        while true {
            let range = NSRange(t.startIndex..., in: t)
            guard let match = timePrefix.firstMatch(in: t, range: range),
                  let matchRange = Range(match.range, in: t)
            else { break }
            t = String(t[matchRange.upperBound...]).trimmingCharacters(in: .whitespaces)
        }

        if t.hasPrefix("?[") && (t.hasSuffix("A") || t.hasSuffix("K")) { return true }
        if t.hasPrefix("WARNING: your terminal doesn't support cursor position requests") { return true }
        // "> test" commit/redraw echoes — not chat content.
        if t == ">" || t.hasPrefix("> ") { return true }
        let stripped = t.trimmingCharacters(in: .whitespaces)
        if stripped.hasPrefix(">") {
            // Keep only if the echo somehow still embeds a chat bracket.
            if !stripped.contains("[") { return true }
        }
        return false
    }
}
