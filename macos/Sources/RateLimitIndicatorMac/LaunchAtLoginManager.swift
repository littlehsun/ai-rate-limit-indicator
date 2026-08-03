import Foundation
import ServiceManagement
import Darwin

enum LaunchAtLoginManager {
    static var isEnabled: Bool {
        let status = SMAppService.mainApp.status
        return status == .enabled || status == .requiresApproval
    }

    static func setEnabled(_ enabled: Bool) throws {
        let service = SMAppService.mainApp
        if enabled {
            switch service.status {
            case .enabled, .requiresApproval:
                return
            case .notRegistered, .notFound:
                try service.register()
            @unknown default:
                try service.register()
            }
        } else {
            switch service.status {
            case .enabled, .requiresApproval:
                try service.unregister()
            case .notRegistered, .notFound:
                return
            @unknown default:
                try service.unregister()
            }
        }
    }

    static func migrateLegacyIfNeeded(
        markerURL: URL = BackendPaths.legacyLoginMigrationMarkerURL
    ) {
        guard FileManager.default.fileExists(atPath: markerURL.path) else { return }
        do {
            try setEnabled(true)
            try retireLegacyAgent()
            try FileManager.default.removeItem(at: markerURL)
        } catch {
            // Keep the marker so the migration is retried on the next launch.
        }
    }

    private static func retireLegacyAgent() throws {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        process.arguments = [
            "bootout",
            "gui/\(getuid())/com.hsun.codex-rate-menubar",
        ]
        do {
            try process.run()
            process.waitUntilExit()
        } catch {
            // Removing the plist still prevents the legacy agent at next login.
        }

        let legacyPlist = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(
                "Library/LaunchAgents/com.hsun.codex-rate-menubar.plist"
            )
        if FileManager.default.fileExists(atPath: legacyPlist.path) {
            try FileManager.default.removeItem(at: legacyPlist)
        }
    }
}
