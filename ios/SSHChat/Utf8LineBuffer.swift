import Foundation
import NIO

/// SSH PTY byte → line splitter that never decodes a partial UTF-8 codepoint.
///
/// `String(buffer:)` on each read chunk turns a split CJK char (e.g. 房 = E6 88 BF
/// cut after 2 bytes) into two replacement characters (`��` / `??`).
struct Utf8LineBuffer {
    private var pending = Data()
    private var text = ""

    mutating func append(_ buffer: ByteBuffer) -> [String] {
        var bb = buffer
        if let bytes = bb.readBytes(length: bb.readableBytes), !bytes.isEmpty {
            pending.append(contentsOf: bytes)
        }
        text.append(Self.takeCompleteUTF8(from: &pending))
        text = text
            .replacingOccurrences(of: "\r\n", with: "\n")
            .replacingOccurrences(of: "\r", with: "\n")
        var lines: [String] = []
        while let range = text.range(of: "\n") {
            lines.append(String(text[..<range.lowerBound]))
            text.removeSubrange(..<range.upperBound)
        }
        return lines
    }

    mutating func finish() -> String? {
        text.append(Self.takeCompleteUTF8(from: &pending))
        if !pending.isEmpty {
            text.append(String(decoding: pending, as: UTF8.self))
            pending.removeAll(keepingCapacity: false)
        }
        text = text
            .replacingOccurrences(of: "\r\n", with: "\n")
            .replacingOccurrences(of: "\r", with: "\n")
        let rest = text
        text = ""
        return rest.isEmpty ? nil : rest
    }

    /// Decode every complete UTF-8 sequence; leave a trailing partial sequence in `data`.
    static func takeCompleteUTF8(from data: inout Data) -> String {
        guard !data.isEmpty else { return "" }
        let hold = trailingIncompleteUTF8Count(data)
        let usable = data.count - hold
        guard usable > 0 else { return "" }
        let slice = data.prefix(usable)
        let s = String(decoding: slice, as: UTF8.self)
        // Rebuild contiguous Data so later 0-based scans stay valid.
        if hold == 0 {
            data.removeAll(keepingCapacity: true)
        } else {
            data = Data(data.suffix(hold))
        }
        return s
    }

    /// How many trailing bytes form an incomplete UTF-8 character (0 if none).
    static func trailingIncompleteUTF8Count(_ data: Data) -> Int {
        // Copy to [UInt8]: Data after removeFirst/prefix may have startIndex != 0,
        // and data[i] with i = count-1 then traps (EXC_BREAKPOINT).
        let bytes = [UInt8](data)
        let n = bytes.count
        guard n > 0 else { return 0 }

        var i = n - 1
        var cont = 0
        while i >= 0 && cont < 3 && (bytes[i] & 0xC0) == 0x80 {
            cont += 1
            i -= 1
        }
        if i < 0 {
            return 0
        }
        let lead = bytes[i]
        let need: Int
        if lead < 0x80 {
            return 0
        } else if lead & 0xE0 == 0xC0 {
            need = 2
        } else if lead & 0xF0 == 0xE0 {
            need = 3
        } else if lead & 0xF8 == 0xF0 {
            need = 4
        } else {
            return 0
        }
        let have = n - i
        return have < need ? have : 0
    }
}
