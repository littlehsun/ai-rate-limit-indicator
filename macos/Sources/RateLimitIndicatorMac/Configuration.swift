import Foundation

enum DisplayMode: String, CaseIterable, Identifiable {
    case auto
    case custom

    var id: String { rawValue }
}

struct DisplayConfiguration: Equatable {
    var mode: DisplayMode
    var enabledProviders: [String]
    var indicatorProviders: [String]
    var dropdownProviders: [String]
    var providerOrder: [String]

    static let defaults = DisplayConfiguration(
        mode: .auto,
        enabledProviders: ProviderCatalog.order,
        indicatorProviders: ProviderCatalog.order,
        dropdownProviders: ProviderCatalog.order,
        providerOrder: ProviderCatalog.order
    )

    func ordered(_ providers: [String]) -> [String] {
        let selected = Set(providers)
        return providerOrder.filter(selected.contains)
    }

    var enabledProviderOrder: [String] {
        providerOrder.filter(enabledProviders.contains)
    }
}

enum ConfigurationStore {
    static func load(from url: URL = BackendPaths.configURL) -> DisplayConfiguration {
        guard let contents = try? String(contentsOf: url, encoding: .utf8) else {
            return .defaults
        }
        var values: [String: String] = [:]
        for line in contents.components(separatedBy: .newlines) {
            let content = line.split(separator: "#", maxSplits: 1).first.map(String.init) ?? ""
            let parts = content.split(separator: "=", maxSplits: 1).map(String.init)
            guard parts.count == 2 else { continue }
            values[parts[0].trimmingCharacters(in: .whitespaces).uppercased()] =
                parts[1].trimmingCharacters(in: .whitespaces)
        }

        let mode = DisplayMode(rawValue: values["DISPLAY_MODE"]?.lowercased() ?? "") ?? .auto
        let enabled = ProviderCatalog.order.filter {
            ["1", "true", "yes", "on"].contains(values[$0.uppercased()]?.lowercased() ?? "")
        }
        let indicator = (providers(from: values["DISPLAY_PROVIDERS"]) ?? enabled)
            .filter(enabled.contains)
        let dropdown = (providers(from: values["DROPDOWN_PROVIDERS"]) ?? enabled)
            .filter(enabled.contains)
        var order = providers(from: values["PROVIDER_ORDER"]) ?? []
        for provider in indicator + dropdown + ProviderCatalog.order where !order.contains(provider) {
            order.append(provider)
        }
        return DisplayConfiguration(
            mode: mode,
            enabledProviders: enabled,
            indicatorProviders: indicator,
            dropdownProviders: dropdown,
            providerOrder: order
        )
    }

    static func save(
        _ configuration: DisplayConfiguration,
        to url: URL = BackendPaths.configURL
    ) throws {
        let updates = [
            "DISPLAY_MODE": configuration.mode.rawValue,
            "DISPLAY_PROVIDERS": configuration.ordered(configuration.indicatorProviders).joined(separator: ","),
            "DROPDOWN_PROVIDERS": configuration.ordered(configuration.dropdownProviders).joined(separator: ","),
            "PROVIDER_ORDER": configuration.providerOrder.joined(separator: ","),
        ]
        let existing = try existingContents(at: url)
        var output: [String] = []
        var written = Set<String>()
        for line in existing.components(separatedBy: .newlines) where !line.isEmpty {
            let content = line.split(separator: "#", maxSplits: 1).first.map(String.init) ?? ""
            let key = content.split(separator: "=", maxSplits: 1).first.map(String.init)?
                .trimmingCharacters(in: .whitespaces).uppercased() ?? ""
            guard let replacement = updates[key] else {
                output.append(line)
                continue
            }
            if !written.contains(key) {
                output.append("\(key)=\(replacement)")
                written.insert(key)
            }
        }
        for key in ["DISPLAY_MODE", "DISPLAY_PROVIDERS", "DROPDOWN_PROVIDERS", "PROVIDER_ORDER"]
        where !written.contains(key) {
            output.append("\(key)=\(updates[key]!)")
        }

        try FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(),
            withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700]
        )
        let temporary = url.deletingLastPathComponent()
            .appendingPathComponent(".\(url.lastPathComponent).tmp")
        try (output.joined(separator: "\n") + "\n").write(
            to: temporary,
            atomically: false,
            encoding: .utf8
        )
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: temporary.path)
        if FileManager.default.fileExists(atPath: url.path) {
            _ = try FileManager.default.replaceItemAt(url, withItemAt: temporary)
        } else {
            try FileManager.default.moveItem(at: temporary, to: url)
        }
    }

    static func existingContents(at url: URL) throws -> String {
        guard FileManager.default.fileExists(atPath: url.path) else { return "" }
        return try String(contentsOf: url, encoding: .utf8)
    }

    private static func providers(from value: String?) -> [String]? {
        guard let value else { return nil }
        return value.lowercased().split(separator: ",")
            .map(String.init)
            .filter(ProviderCatalog.order.contains)
    }
}
