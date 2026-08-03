import AppKit
import SwiftUI

private enum UsageColor {
    static func color(for value: Int) -> Color {
        if value >= 90 { return Color(red: 1.0, green: 0.33, blue: 0.33) }
        if value >= 70 { return Color(red: 1.0, green: 0.72, blue: 0.18) }
        return Color(red: 0.0, green: 0.69, blue: 0.31)
    }
}

struct ProviderIcon: View {
    let provider: String
    var size: CGFloat = 17

    var body: some View {
        if let image = providerImage(provider) {
            Image(nsImage: image)
                .resizable()
                .aspectRatio(contentMode: .fit)
                .frame(width: size, height: size)
        } else {
            Circle()
                .fill(.secondary)
                .frame(width: size, height: size)
                .overlay {
                    Text(String(ProviderCatalog.label(for: provider).prefix(1)))
                        .font(.system(size: size * 0.55, weight: .bold))
                        .foregroundStyle(.white)
                }
        }
    }

    private func providerImage(_ provider: String) -> NSImage? {
        let names = [
            "codex": "codex-logo.png",
            "claude": "claude-logo.svg",
            "grok": "grok-logo.png",
            "gemini": "gemini-logo.svg",
        ]
        guard let name = names[provider] else { return nil }
        guard let image = NSImage(
            contentsOf: BackendPaths.assetsURL.appendingPathComponent(name)
        ) else {
            return nil
        }
        image.size = NSSize(width: size, height: size)
        return image
    }
}

struct MenuBarLabel: View {
    let snapshots: [ProviderSnapshot]

    var body: some View {
        if snapshots.isEmpty {
            Text("--")
                .monospacedDigit()
        } else if snapshots.count == 1, let snapshot = snapshots.first {
            ProviderStatusLabel(snapshot: snapshot)
        } else if let image = MenuBarCompositeImage.make(for: snapshots) {
            Image(nsImage: image)
                .resizable()
                .interpolation(.high)
                .frame(width: image.size.width, height: image.size.height)
                .help(MenuBarCompositeImage.helpText(for: snapshots))
        } else {
            Text("--")
                .monospacedDigit()
        }
    }
}

private enum MenuBarCompositeImage {
    private static let iconSize: CGFloat = 13
    private static let height: CGFloat = 16
    private static let iconTextSpacing: CGFloat = 3
    private static let entrySpacing: CGFloat = 7
    private static let font = NSFont.monospacedDigitSystemFont(ofSize: 11, weight: .medium)

    static func make(for snapshots: [ProviderSnapshot]) -> NSImage? {
        let entries = snapshots.map(entry(for:))
        let widths = entries.map { entry in
            iconSize + iconTextSpacing + ceil(entry.text.size(withAttributes: entry.attributes).width)
        }
        let width = widths.reduce(0, +)
            + entrySpacing * CGFloat(max(entries.count - 1, 0))
        guard width > 0 else { return nil }

        let image = NSImage(size: NSSize(width: width, height: height), flipped: false) { _ in
            var cursor: CGFloat = 0
            for (index, entry) in entries.enumerated() {
                if index > 0 {
                    cursor += entrySpacing / 2
                    NSColor.labelColor.withAlphaComponent(0.65).setFill()
                    NSRect(x: cursor, y: 2, width: 1, height: height - 4).fill()
                    cursor += entrySpacing / 2
                }

                if let logo = providerImage(entry.provider) {
                    logo.draw(
                        in: NSRect(
                            x: cursor,
                            y: (height - iconSize) / 2,
                            width: iconSize,
                            height: iconSize
                        ),
                        from: .zero,
                        operation: .sourceOver,
                        fraction: 1
                    )
                }
                cursor += iconSize + iconTextSpacing
                entry.text.draw(
                    at: NSPoint(x: cursor, y: 1),
                    withAttributes: entry.attributes
                )
                cursor += ceil(entry.text.size(withAttributes: entry.attributes).width)
            }
            return true
        }
        image.isTemplate = false
        return image
    }

    static func helpText(for snapshots: [ProviderSnapshot]) -> String {
        snapshots.map { snapshot in
            guard let window = primaryWindow(snapshot) else {
                return "\(snapshot.label): no data"
            }
            let stale = snapshot.status == "stale" ? "cached " : ""
            return "\(snapshot.label): \(stale)\(window.usedPercent)%"
        }.joined(separator: ", ")
    }

    private static func entry(for snapshot: ProviderSnapshot) -> CompositeEntry {
        guard let window = primaryWindow(snapshot) else {
            return CompositeEntry(
                provider: snapshot.provider,
                text: "--" as NSString,
                attributes: textAttributes(color: .secondaryLabelColor)
            )
        }
        let stale = snapshot.status == "stale" ? "~" : ""
        return CompositeEntry(
            provider: snapshot.provider,
            text: "\(stale)\(window.usedPercent)%" as NSString,
            attributes: textAttributes(color: usageColor(for: window.usedPercent))
        )
    }

    private static func primaryWindow(_ snapshot: ProviderSnapshot) -> UsageWindow? {
        snapshot.windows
            .filter(\.isSevenDay)
            .max(by: { $0.usedPercent < $1.usedPercent })
            ?? snapshot.windows.first
    }

    private static func providerImage(_ provider: String) -> NSImage? {
        let names = [
            "codex": "codex-logo.png",
            "claude": "claude-logo.svg",
            "grok": "grok-logo.png",
            "gemini": "gemini-logo.svg",
        ]
        guard let name = names[provider] else { return nil }
        return NSImage(contentsOf: BackendPaths.assetsURL.appendingPathComponent(name))
    }

    private static func textAttributes(color: NSColor) -> [NSAttributedString.Key: Any] {
        [
            .font: font,
            .foregroundColor: color,
        ]
    }

    private static func usageColor(for value: Int) -> NSColor {
        if value >= 90 { return NSColor(calibratedRed: 1, green: 0.33, blue: 0.33, alpha: 1) }
        if value >= 70 { return NSColor(calibratedRed: 1, green: 0.72, blue: 0.18, alpha: 1) }
        return NSColor(calibratedRed: 0, green: 0.69, blue: 0.31, alpha: 1)
    }

    private struct CompositeEntry {
        let provider: String
        let text: NSString
        let attributes: [NSAttributedString.Key: Any]
    }
}

private struct ProviderStatusLabel: View {
    let snapshot: ProviderSnapshot

    var body: some View {
        HStack(spacing: 4) {
            ProviderIcon(provider: snapshot.provider, size: 13)
            if snapshot.windows.isEmpty {
                Text("--")
                    .foregroundStyle(.secondary)
            } else {
                ForEach(Array(snapshot.indicatorDisplayWindows.enumerated()), id: \.element.id) { index, window in
                    if index > 0 {
                        Text("|")
                            .foregroundStyle(.gray)
                    }
                    Text("\(index == 0 ? stalePrefix : "")\(window.usedPercent)%")
                        .foregroundStyle(UsageColor.color(for: window.usedPercent))
                        .monospacedDigit()
                }
                if let constrained = snapshot.indicatorResetWindow {
                    Text("⟳\(UsageFormatting.countdown(to: constrained.resetsAt))")
                        .foregroundStyle(.secondary)
                        .monospacedDigit()
                }
            }
        }
        .font(.system(size: 11, weight: .medium, design: .monospaced))
        .help(statusHelp)
    }

    private var statusHelp: String {
        if snapshot.status == "stale" {
            return "\(snapshot.label): cached data; sign in to the provider to refresh it."
        }
        return snapshot.label
    }

    private var stalePrefix: String {
        snapshot.status == "stale" ? "~" : ""
    }
}

struct MenuContentView: View {
    @ObservedObject var model: AppModel
    @Environment(\.openSettings) private var openSettings

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let migrationError = LaunchAtLoginManager.migrationErrorMessage {
                Text(migrationError)
                    .font(.callout)
                    .foregroundStyle(.red)
                    .padding()
            }

            if let error = model.errorMessage, model.snapshots.isEmpty {
                Text(error)
                    .font(.callout)
                    .foregroundStyle(.red)
                    .padding()
            }

            if model.isRefreshing && model.snapshots.isEmpty {
                VStack(spacing: 10) {
                    ProgressView()
                    Text("Loading provider usage…")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, minHeight: 140)
            } else if model.dropdownSnapshots.isEmpty {
                VStack(spacing: 10) {
                    Image(systemName: "rectangle.stack.badge.minus")
                        .font(.system(size: 28))
                        .foregroundStyle(.secondary)
                    Text("No providers shown in the menu panel")
                        .font(.headline)
                    Text("Choose at least one provider in Display settings.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                    Button("Open Display settings…", action: showSettings)
                        .buttonStyle(.borderedProminent)
                }
                .frame(maxWidth: .infinity, minHeight: 160)
                .padding(.horizontal, 20)
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 0) {
                        ForEach(Array(model.dropdownSnapshots.enumerated()), id: \.element.id) { index, snapshot in
                            if index > 0 {
                                Divider()
                                    .padding(.horizontal, 16)
                            }
                            ProviderSectionView(snapshot: snapshot, model: model)
                        }
                    }
                }
                .frame(height: providerListHeight)
            }

            Divider()
            HStack {
                Button("Display settings…", action: showSettings)
                .buttonStyle(.plain)
                Spacer()
                Button {
                    Task { await model.refresh() }
                } label: {
                    if model.isRefreshing {
                        ProgressView()
                            .controlSize(.small)
                    } else {
                        Image(systemName: "arrow.clockwise")
                    }
                }
                .buttonStyle(.borderless)
                .help("Refresh")
                Button("Quit") {
                    NSApplication.shared.terminate(nil)
                }
                .buttonStyle(.plain)
            }
            .padding(14)
        }
        .frame(width: 440)
    }

    private func showSettings() {
        NSApp.activate()
        openSettings()
    }

    private var providerListHeight: CGFloat {
        min(max(CGFloat(model.dropdownSnapshots.count) * 120, 160), 520)
    }
}

private struct ProviderSectionView: View {
    let snapshot: ProviderSnapshot
    @ObservedObject var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Button {
                model.selectProviderFromDropdown(snapshot.provider)
            } label: {
                HStack(spacing: 7) {
                    Image(systemName: model.isSelectedForIndicator(snapshot.provider) ? "checkmark" : "")
                        .frame(width: 12)
                    ProviderIcon(provider: snapshot.provider, size: 18)
                    Text(snapshot.label + statusSuffix)
                        .font(.headline)
                    Spacer()
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            if let error = snapshot.error {
                InfoLine(text: "Error: \(error)", color: .red)
            } else if snapshot.windows.isEmpty {
                InfoLine(text: "No data")
            }

            ForEach(snapshot.windows) { window in
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Image(systemName: window.isSevenDay || window.id == "monthly" ? "calendar" : "bolt.fill")
                        .frame(width: 15)
                        .foregroundStyle(window.isSevenDay || window.id == "monthly" ? Color.gray : Color.yellow)
                    Text("\(window.label):")
                    if let detail = window.detail {
                        Text("\(detail) (\(window.usedPercent)%)")
                    } else {
                        Text("\(window.usedPercent)%")
                            .foregroundStyle(UsageColor.color(for: window.usedPercent))
                    }
                    if window.resetsAt != nil {
                        Text("⟳ \(UsageFormatting.resetDate(window.resetsAt)) (\(UsageFormatting.countdown(to: window.resetsAt)))")
                            .foregroundStyle(.secondary)
                    }
                }
                .font(.system(.callout, design: .monospaced))
            }

            ExtrasView(extras: snapshot.extras)

            if snapshot.updatedAt != nil {
                InfoLine(text: "Updated: \(UsageFormatting.updatedAt(snapshot.updatedAt))")
            }
        }
        .padding(16)
    }

    private var statusSuffix: String {
        ["error", "no_data", "stale"].contains(snapshot.status)
            ? " (\(snapshot.status.replacingOccurrences(of: "_", with: " ")))"
            : ""
    }
}

private struct ExtrasView: View {
    let extras: [String]
    @State private var resetExpanded = false

    var body: some View {
        let reset = extras.first { $0.hasPrefix("Reset credits:") }
        let expirations = extras.filter { $0.range(of: #"^\d+\. expires "#, options: .regularExpression) != nil }
        let remaining = extras.filter { value in
            value != reset && !expirations.contains(value)
        }

        if let reset {
            if expirations.isEmpty {
                InfoLine(text: reset)
            } else {
                DisclosureGroup(isExpanded: $resetExpanded) {
                    VStack(alignment: .leading, spacing: 6) {
                        ForEach(expirations, id: \.self) { expiration in
                            Text(expiration)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .padding(.top, 5)
                    .padding(.leading, 18)
                } label: {
                    Text(reset)
                }
                .font(.system(.callout, design: .monospaced))
            }
        }
        ForEach(remaining, id: \.self) { extra in
            InfoLine(text: extra)
        }
    }
}

private struct InfoLine: View {
    let text: String
    var color: Color = Color(nsColor: .secondaryLabelColor)

    var body: some View {
        Text(text)
            .font(.system(.callout, design: .monospaced))
            .foregroundStyle(color)
    }
}

struct SettingsView: View {
    @ObservedObject var model: AppModel
    @State private var launchAtLogin = LaunchAtLoginManager.isEnabled
    @State private var launchError: String? = LaunchAtLoginManager.migrationErrorMessage

    var body: some View {
        Form {
            if let error = model.configurationErrorMessage {
                Section {
                    Text(error)
                        .foregroundStyle(.red)
                }
            }

            Section {
                Picker("Display mode", selection: modeBinding) {
                    Text("Automatic").tag(DisplayMode.auto)
                    Text("Choose providers").tag(DisplayMode.custom)
                }
                .pickerStyle(.segmented)

                Text(modeDescription)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } header: {
                Text("Menu bar")
            } footer: {
                Text("Click the usage display in the macOS menu bar to open the provider panel.")
            }

            Section {
                providerMenu(
                    title: "Show in menu bar",
                    providers: model.configuration.indicatorProviders
                ) { provider in
                    model.toggleIndicator(provider)
                }

                providerMenu(
                    title: "Show in menu panel",
                    providers: model.configuration.dropdownProviders
                ) { provider in
                    model.toggleDropdown(provider)
                }

                LabeledContent("Display order") {
                    VStack(alignment: .trailing, spacing: 6) {
                        ForEach(
                            Array(model.configuration.enabledProviderOrder.enumerated()),
                            id: \.element
                        ) { index, provider in
                            HStack(spacing: 6) {
                                ProviderIcon(provider: provider, size: 15)
                                Text(ProviderCatalog.label(for: provider))
                                Spacer()
                                Button {
                                    model.moveProvider(provider, offset: -1)
                                } label: {
                                    Image(systemName: "chevron.up")
                                }
                                .disabled(index == 0)
                                .accessibilityLabel("Move \(ProviderCatalog.label(for: provider)) up")
                                Button {
                                    model.moveProvider(provider, offset: 1)
                                } label: {
                                    Image(systemName: "chevron.down")
                                }
                                .disabled(index == model.configuration.enabledProviderOrder.count - 1)
                                .accessibilityLabel("Move \(ProviderCatalog.label(for: provider)) down")
                            }
                            .frame(width: 270)
                        }
                    }
                }
            } header: {
                Text("Providers")
            } footer: {
                if model.configuration.enabledProviders.isEmpty {
                    Text("No data sources are enabled. Enable a provider flag in the configuration file first.")
                } else {
                    Text("Menu panel controls the provider sections shown after clicking the menu bar item.")
                }
            }

            Section("Startup") {
                Toggle("Launch at login", isOn: Binding(
                    get: { launchAtLogin },
                    set: { enabled in
                        do {
                            try LaunchAtLoginManager.setEnabled(enabled)
                            launchAtLogin = enabled
                            launchError = nil
                        } catch {
                            launchError = error.localizedDescription
                        }
                    }
                ))
                if let launchError {
                    Text(launchError)
                        .foregroundStyle(.red)
                }
            }
        }
        .formStyle(.grouped)
        .padding()
        .frame(width: 600, height: 500)
    }

    private func providerMenu(
        title: String,
        providers: [String],
        toggle: @escaping (String) -> Void
    ) -> some View {
        LabeledContent(title) {
            Menu {
                ForEach(model.configuration.enabledProviderOrder, id: \.self) { provider in
                    Button {
                        toggle(provider)
                    } label: {
                        if providers.contains(provider) {
                            Label(
                                ProviderCatalog.label(for: provider),
                                systemImage: "checkmark"
                            )
                        } else {
                            Text(ProviderCatalog.label(for: provider))
                        }
                    }
                }
            } label: {
                HStack(spacing: 6) {
                    Text(providerSummary(providers))
                        .lineLimit(1)
                    Image(systemName: "chevron.down")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .frame(minWidth: 220, alignment: .trailing)
            }
            .menuStyle(.borderlessButton)
            .accessibilityLabel(title)
        }
    }

    private func providerSummary(_ providers: [String]) -> String {
        let ordered = model.configuration.enabledProviderOrder.filter(providers.contains)
        if ordered.isEmpty {
            return "None"
        }
        if ordered.count == model.configuration.enabledProviders.count {
            return "All providers"
        }
        return ordered.map(ProviderCatalog.label(for:)).joined(separator: ", ")
    }

    private var modeDescription: String {
        switch model.configuration.mode {
        case .auto:
            "Shows the provider with the most recent meaningful 7D usage change."
        case .custom:
            "Shows every provider checked in the Menu bar column."
        }
    }

    private var modeBinding: Binding<DisplayMode> {
        Binding(
            get: { model.configuration.mode },
            set: model.setMode
        )
    }

}
