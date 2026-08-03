import Foundation
import XCTest
@testable import RateLimitIndicatorMac

final class BackendContractTests: XCTestCase {
    func testDecodesSharedNormalizedSnapshot() throws {
        let json = """
        {
          "providers": [{
            "provider": "codex",
            "label": "Codex",
            "updated_at": "2026-07-30T08:05:02Z",
            "windows": [{
              "id": "7d",
              "label": "7D",
              "used_percent": 41,
              "resets_at": 1785902956,
              "detail": null
            }],
            "status": "fresh",
            "error": null,
            "extras": ["Reset credits: 1"]
          }]
        }
        """
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase

        let payload = try decoder.decode(SnapshotPayload.self, from: Data(json.utf8))

        XCTAssertEqual(payload.providers.first?.sevenDayPercent, 41)
        XCTAssertEqual(payload.providers.first?.extras, ["Reset credits: 1"])
    }

    func testAutoSelectsProviderWithLargestSevenDayChange() {
        let selector = AutoDisplaySelector()
        _ = selector.choose(from: [
            snapshot(provider: "codex", percent: 20, updatedAt: "2026-07-30T05:00:00Z"),
            snapshot(provider: "grok", percent: 40, updatedAt: "2026-07-30T05:01:00Z"),
        ])

        let selected = selector.choose(from: [
            snapshot(provider: "codex", percent: 30, updatedAt: "2026-07-30T05:02:00Z"),
            snapshot(provider: "grok", percent: 41, updatedAt: "2026-07-30T05:03:00Z"),
        ])

        XCTAssertEqual(selected?.provider, "codex")
    }

    func testIndicatorResetUsesShortestQuotaWindow() {
        let fiveHour = UsageWindow(
            id: "5h",
            label: "5H",
            usedPercent: 10,
            resetsAt: 100,
            detail: nil
        )
        let weekly = UsageWindow(
            id: "7d",
            label: "7D",
            usedPercent: 80,
            resetsAt: 200,
            detail: nil
        )
        let monthly = UsageWindow(
            id: "monthly",
            label: "Monthly",
            usedPercent: 90,
            resetsAt: 300,
            detail: nil
        )

        XCTAssertEqual(
            snapshot(provider: "codex", windows: [weekly, fiveHour]).indicatorResetWindow,
            fiveHour
        )
        XCTAssertEqual(
            snapshot(provider: "grok", windows: [monthly, weekly]).indicatorResetWindow,
            weekly
        )
    }

    func testSnapshotCanBeMarkedStaleAfterRefreshFailure() {
        let fresh = snapshot(
            provider: "codex",
            percent: 42,
            updatedAt: "2026-07-30T05:00:00Z"
        )

        let stale = fresh.markingStale()

        XCTAssertEqual(stale.status, "stale")
        XCTAssertEqual(stale.windows, fresh.windows)
        XCTAssertEqual(stale.updatedAt, fresh.updatedAt)
    }

    func testCustomConfigPathResolution() {
        let embedded = BackendPaths.resolveConfigURL(
            environment: [:],
            embeddedPath: "/tmp/custom providers.env"
        )
        let overridden = BackendPaths.resolveConfigURL(
            environment: ["RATE_LIMIT_INDICATOR_CONFIG": "/tmp/override.env"],
            embeddedPath: "/tmp/custom providers.env"
        )

        XCTAssertEqual(embedded.path, "/tmp/custom providers.env")
        XCTAssertEqual(overridden.path, "/tmp/override.env")
    }

    private func snapshot(
        provider: String,
        percent: Int,
        updatedAt: String
    ) -> ProviderSnapshot {
        ProviderSnapshot(
            provider: provider,
            label: ProviderCatalog.label(for: provider),
            updatedAt: updatedAt,
            windows: [
                UsageWindow(
                    id: "7d",
                    label: "7D",
                    usedPercent: percent,
                    resetsAt: nil,
                    detail: nil
                ),
            ],
            status: "fresh",
            error: nil,
            extras: []
        )
    }

    private func snapshot(
        provider: String,
        windows: [UsageWindow]
    ) -> ProviderSnapshot {
        ProviderSnapshot(
            provider: provider,
            label: ProviderCatalog.label(for: provider),
            updatedAt: nil,
            windows: windows,
            status: "fresh",
            error: nil,
            extras: []
        )
    }
}
