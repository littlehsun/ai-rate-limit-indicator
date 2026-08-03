import Foundation
import ServiceManagement

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
            try FileManager.default.removeItem(at: markerURL)
        } catch {
            // Keep the marker so the migration is retried on the next launch.
        }
    }
}
