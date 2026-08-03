import Foundation

enum BackendError: LocalizedError {
    case missingCLI(String)
    case failed(String)
    case invalidResponse(String)

    var errorDescription: String? {
        switch self {
        case let .missingCLI(path):
            "Unified backend is missing at \(path). Run the macOS installer again."
        case let .failed(message):
            message
        case let .invalidResponse(message):
            "Unified backend returned invalid data: \(message)"
        }
    }
}

struct BackendClient {
    let cliURL: URL
    let configURL: URL
    let pythonURL: URL

    init(
        cliURL: URL = BackendPaths.cliURL,
        configURL: URL = BackendPaths.configURL,
        pythonURL: URL = BackendPaths.pythonURL
    ) {
        self.cliURL = cliURL
        self.configURL = configURL
        self.pythonURL = pythonURL
    }

    func fetchSnapshots() async throws -> [ProviderSnapshot] {
        let cliURL = self.cliURL
        let configURL = self.configURL
        let pythonURL = self.pythonURL
        return try await Task.detached(priority: .utility) {
            guard FileManager.default.fileExists(atPath: cliURL.path) else {
                throw BackendError.missingCLI(cliURL.path)
            }

            let process = Process()
            let standardOutput = Pipe()
            let standardError = Pipe()
            process.executableURL = pythonURL
            process.arguments = [cliURL.path, "--json"]
            var environment = ProcessInfo.processInfo.environment
            environment["RATE_LIMIT_INDICATOR_CONFIG"] = configURL.path
            process.environment = environment
            process.standardOutput = standardOutput
            process.standardError = standardError

            do {
                try process.run()
            } catch {
                throw BackendError.failed("Could not start the unified backend: \(error.localizedDescription)")
            }
            process.waitUntilExit()

            let output = standardOutput.fileHandleForReading.readDataToEndOfFile()
            let errorOutput = standardError.fileHandleForReading.readDataToEndOfFile()
            guard process.terminationStatus == 0 else {
                let detail = String(data: errorOutput, encoding: .utf8)?
                    .trimmingCharacters(in: .whitespacesAndNewlines)
                throw BackendError.failed(
                    detail?.isEmpty == false ? detail! : "Unified backend exited with status \(process.terminationStatus)"
                )
            }

            do {
                let decoder = JSONDecoder()
                decoder.keyDecodingStrategy = .convertFromSnakeCase
                return try decoder.decode(SnapshotPayload.self, from: output).providers
            } catch {
                throw BackendError.invalidResponse(error.localizedDescription)
            }
        }.value
    }
}

enum BackendPaths {
    static let appSupportURL = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Application Support/RateLimitIndicator", isDirectory: true)
    static let backendURL = appSupportURL.appendingPathComponent("backend", isDirectory: true)
    static let cliURL = backendURL.appendingPathComponent("cli.py")
    static let assetsURL = appSupportURL.appendingPathComponent("assets", isDirectory: true)
    static let legacyLoginMigrationMarkerURL = appSupportURL
        .appendingPathComponent("migrate-legacy-launch-at-login")
    static var pythonURL: URL {
        resolvePythonURL(
            environment: ProcessInfo.processInfo.environment,
            embeddedPath: Bundle.main.object(
                forInfoDictionaryKey: "RateLimitIndicatorPythonPath"
            ) as? String
        )
    }
    static var configURL: URL {
        resolveConfigURL(
            environment: ProcessInfo.processInfo.environment,
            embeddedPath: Bundle.main.object(
                forInfoDictionaryKey: "RateLimitIndicatorConfigPath"
            ) as? String
        )
    }

    static func resolveConfigURL(
        environment: [String: String],
        embeddedPath: String?
    ) -> URL {
        if let path = environment["RATE_LIMIT_INDICATOR_CONFIG"], !path.isEmpty {
            return URL(fileURLWithPath: path)
        }
        if let path = embeddedPath, !path.isEmpty {
            return URL(fileURLWithPath: path)
        }
        return FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".config/rate-limit-indicator/providers.env")
    }

    static func resolvePythonURL(
        environment: [String: String],
        embeddedPath: String?
    ) -> URL {
        if let path = environment["RATE_LIMIT_INDICATOR_PYTHON"], !path.isEmpty {
            return URL(fileURLWithPath: path)
        }
        if let path = embeddedPath, !path.isEmpty {
            return URL(fileURLWithPath: path)
        }
        return URL(fileURLWithPath: "/usr/bin/python3")
    }
}
