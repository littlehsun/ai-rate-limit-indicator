import Foundation

struct UsageWindow: Codable, Identifiable, Hashable {
    let id: String
    let label: String
    let usedPercent: Int?
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
        windows.compactMap(\.usedPercent).max() ?? 0
    }

    var sevenDayPercent: Int? {
        windows.filter(\.isSevenDay).compactMap(\.usedPercent).max()
    }

    /// The countdown belongs to a window the backend actually reported. A slot
    /// held open for a window nobody sent has no reset to count down to.
    var indicatorResetWindow: UsageWindow? {
        indicatorDisplayWindows
            .filter { $0.usedPercent != nil && $0.resetsAt != nil }
            .min { $0.quotaCadenceRank < $1.quotaCadenceRank }
    }

    /// Providers that report more than one quota group, and the two windows the
    /// panel shows for them. Antigravity sends a Gemini group and a Claude/GPT
    /// one; taking the first two windows positionally meant that when Gemini's
    /// weekly quota filled, Antigravity stopped reporting its five-hour bucket
    /// and the slice slid into the next group — the panel then showed
    /// Claude/GPT's number under Gemini's name, with Claude/GPT's countdown.
    ///
    /// Claude is listed even though it reports one group. `_parse_credentials`
    /// appends its two windows independently and only fails when both are
    /// absent, so a payload carrying `five_hour` without `seven_day` yields a
    /// one-window snapshot — and the panel should hold the empty slot there
    /// too, the way indicator.py already does.
    static let panelWindowIDs: [String: [String]] = [
        "claude": ["5h", "7d"],
        "gemini": ["5h", "7d"],
    ]

    var indicatorDisplayWindows: [UsageWindow] {
        if let wanted = Self.panelWindowIDs[provider] {
            // A window whose quota is spent disappears from the payload
            // entirely, so the slot is held open rather than letting the row
            // shorten and every number beside it shift.
            return wanted.map { id in
                windows.first { $0.id == id }
                    ?? UsageWindow(
                        id: id,
                        label: id.uppercased(),
                        usedPercent: nil,
                        resetsAt: nil,
                        detail: nil
                    )
            }
        }

        var displayed = Array(windows.prefix(2))
        guard let strongestSevenDay = windows
            .filter(\.isSevenDay)
            .max(by: { ($0.usedPercent ?? -1) < ($1.usedPercent ?? -1) }),
              !displayed.contains(strongestSevenDay) else {
            return displayed
        }
        if displayed.count < 2 {
            displayed.append(strongestSevenDay)
        } else {
            displayed[displayed.count - 1] = strongestSevenDay
        }
        return displayed
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
    /// A window the backend did not report has no percentage. "--" says so;
    /// "0%" would claim the quota is untouched when in fact it is spent, which
    /// is exactly when Antigravity stops reporting a window.
    static func percent(_ value: Int?) -> String {
        guard let value else { return "--" }
        return "\(value)%"
    }

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
