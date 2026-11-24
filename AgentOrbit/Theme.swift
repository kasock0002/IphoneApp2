import SwiftUI

enum OrbitTheme {
    static let background = Color(hex: "0A0F1B")
    static let surface = Color(hex: "141D2D")
    static let secondary = Color(hex: "A6B3C9")
    static let accent = Color(hex: "92F2C6")
}

extension Color {
    init(hex: String) {
        let value = UInt64(hex.trimmingCharacters(in: CharacterSet.alphanumerics.inverted), radix: 16) ?? 0x92F2C6
        self.init(.sRGB,
                  red: Double((value >> 16) & 0xFF) / 255,
                  green: Double((value >> 8) & 0xFF) / 255,
                  blue: Double(value & 0xFF) / 255,
                  opacity: 1)
    }
}
