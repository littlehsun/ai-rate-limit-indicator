import Foundation
import ServiceManagement
import Darwin

enum LaunchAtLoginError: LocalizedError {
    case approvalRequired

    var errorDescription: String? {
        "Approve Rate Limit Indicator in System Settings > General > Login Items, then reopen the app."
    }
}

enum LaunchAtLoginManager {
    private(set) static var migrationErrorMessage: String?

    static var isEnabled: Bool {
        SMAppService.mainApp.status == .enabled
    }

    static func setEnabled(_ enabled: Bool) throws {
        let service = SMAppService.mainApp
        if enabled {
            switch service.status {
            case .enabled:
                return
            case .requiresApproval:
                throw LaunchAtLoginError.approvalRequired
            case .notRegistered, .notFound:
                try service.register()
            @unknown default:
                try service.register()
            }
            guard service.status == .enabled else {
                throw LaunchAtLoginError.approvalRequired
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
            migrationErrorMessage = nil
        } catch {
            // Keep the marker so the migration is retried on the next launch.
            migrationErrorMessage = error.localizedDescription
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
