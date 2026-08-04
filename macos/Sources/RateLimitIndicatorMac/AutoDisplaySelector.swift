import Foundation

final class AutoDisplaySelector {
    private var lastValues: [String: Int] = [:]
    private var selectedProvider: String?

    func choose(from snapshots: [ProviderSnapshot]) -> ProviderSnapshot? {
        let eligible = snapshots.compactMap { snapshot -> (ProviderSnapshot, Int, Int)? in
            guard snapshot.status == "fresh", let value = snapshot.sevenDayPercent else {
                return nil
            }
            return (snapshot, value, updatedTimestamp(snapshot.updatedAt))
        }
        guard !eligible.isEmpty else {
            if let selectedProvider,
               let retained = snapshots.first(where: {
                   $0.provider == selectedProvider && $0.status == "stale"
               }) {
                return retained
            }
            let candidates = snapshots.filter { $0.status == "fresh" }
            return (candidates.isEmpty ? snapshots : candidates)
                .max { updatedTimestamp($0.updatedAt) < updatedTimestamp($1.updatedAt) }
        }

        let changed = eligible.compactMap { snapshot, value, updatedAt -> (Int, Int, ProviderSnapshot)? in
            guard let previous = lastValues[snapshot.provider], previous != value else {
                return nil
            }
            return (abs(value - previous), updatedAt, snapshot)
        }
        let selected: ProviderSnapshot
        if let change = changed.max(by: {
            ($0.0, $0.1) < ($1.0, $1.1)
        }) {
            selected = change.2
        } else if let current = eligible.first(where: { $0.0.provider == selectedProvider }) {
            selected = current.0
        } else {
            selected = eligible.max(by: { $0.2 < $1.2 })!.0
        }
        lastValues.merge(eligible.map { ($0.0.provider, $0.1) }) { _, new in new }
        selectedProvider = selected.provider
        return selected
    }

    private func updatedTimestamp(_ value: String?) -> Int {
        guard let value else { return 0 }
        let parser = ISO8601DateFormatter()
        parser.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = parser.date(from: value) {
            return Int(date.timeIntervalSince1970)
        }
        parser.formatOptions = [.withInternetDateTime]
        return Int(parser.date(from: value)?.timeIntervalSince1970 ?? 0)
    }
}
