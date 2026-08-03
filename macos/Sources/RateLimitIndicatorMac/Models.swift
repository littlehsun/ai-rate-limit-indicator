import Foundation

struct UsageWindow: Codable, Identifiable, Hashable {
    let id: String
    let label: String
    let usedPercent: Int
    let resetsAt: Int?
    let detail: String?

    var isSevenDay: Bool {
        let normalized = id.lowercased()
        return normalized == "7d"
            || normalized == "weekly"
            || normalized.hasSuffix("-7d")
    }

    var quotaCadenceRank: Int {
        let normalized = id.lowercased()
        if isSevenDay || normalized.hasSuffix("-weekly") { return 1 }
        if normalized == "monthly" || normalized.hasSuffix("-monthly") { return 2 }
        return 0
    }
}

struct ProviderSnapshot: Codable, Identifiable, Hashable {
    var id: String { provider }

    let provider: String
    let label: String
    let updatedAt: String?
    let windows: [UsageWindow]
    let status: String
    let error: String?
    let extras: [String]

    var maxUsedPercent: Int {
        windows.map(\.usedPercent).max() ?? 0
    }

    var sevenDayPercent: Int? {
        windows.filter(\.isSevenDay).map(\.usedPercent).max()
    }

    var indicatorResetWindow: UsageWindow? {
        windows.prefix(2).min {
            $0.quotaCadenceRank < $1.quotaCadenceRank
        }
    }

    func markingStale() -> ProviderSnapshot {
        ProviderSnapshot(
            provider: provider,
            label: label,
            updatedAt: updatedAt,
            windows: windows,
            status: "stale",
            error: error,
            extras: extras
        )
    }
}

struct SnapshotPayload: Codable {
    let providers: [ProviderSnapshot]
}

enum ProviderCatalog {
    static let order = ["codex", "claude", "grok", "gemini"]
    static let labels = [
        "codex": "Codex",
        "claude": "Claude",
        "grok": "Grok",
        "gemini": "Gemini",
    ]

    static func label(for provider: String) -> String {
        labels[provider] ?? provider.capitalized
    }
}

enum UsageFormatting {
    static func countdown(to timestamp: Int?, now: Date = Date()) -> String {
        guard let timestamp else { return "--" }
        let seconds = timestamp - Int(now.timeIntervalSince1970)
        guard seconds > 0 else { return "soon" }
        let days = seconds / 86_400
        let hours = (seconds % 86_400) / 3_600
        let minutes = (seconds % 3_600) / 60
        if days > 0 { return "\(days)d\(hours)h" }
        if hours > 0 { return "\(hours)h\(minutes)m" }
        return "\(minutes)m"
    }

    static func resetDate(_ timestamp: Int?) -> String {
        guard let timestamp else { return "--" }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd HH:mm"
        return formatter.string(from: Date(timeIntervalSince1970: TimeInterval(timestamp)))
    }

    static func updatedAt(_ value: String?) -> String {
        guard let value else { return "--" }
        let parser = ISO8601DateFormatter()
        parser.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let date = parser.date(from: value) ?? {
            parser.formatOptions = [.withInternetDateTime]
            return parser.date(from: value)
        }()
        guard let date else {
            return String(value.replacingOccurrences(of: "T", with: " ").prefix(16))
        }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd HH:mm"
        return formatter.string(from: date)
    }
}
