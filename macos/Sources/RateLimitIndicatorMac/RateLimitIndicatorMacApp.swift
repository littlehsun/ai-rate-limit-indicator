import SwiftUI

@main
struct RateLimitIndicatorMacApp: App {
    @StateObject private var model = AppModel()

    init() {
        LaunchAtLoginManager.migrateLegacyIfNeeded()
    }

    var body: some Scene {
        MenuBarExtra {
            MenuContentView(model: model)
        } label: {
            MenuBarLabel(snapshots: model.indicatorSnapshots)
        }
        .menuBarExtraStyle(.window)

        Settings {
            SettingsView(model: model)
        }
    }
}
