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
        return NSImage(contentsOf: BackendPaths.assetsURL.appendingPathComponent(name))
    }
}

struct MenuBarLabel: View {
    let snapshots: [ProviderSnapshot]

    var body: some View {
        HStack(spacing: 7) {
            if snapshots.isEmpty {
                Text("--")
                    .monospacedDigit()
            } else {
                ForEach(Array(snapshots.enumerated()), id: \.element.id) { index, snapshot in
                    if index > 0 {
                        Rectangle()
                            .fill(Color.gray)
                            .frame(width: 1, height: 14)
                    }
                    ProviderStatusLabel(snapshot: snapshot)
                }
            }
        }
    }
}

private struct ProviderStatusLabel: View {
    let snapshot: ProviderSnapshot

    var body: some View {
        HStack(spacing: 4) {
            ProviderIcon(provider: snapshot.provider)
            if snapshot.windows.isEmpty {
                Text("--")
                    .foregroundStyle(.secondary)
            } else {
                ForEach(Array(snapshot.windows.prefix(2).enumerated()), id: \.element.id) { index, window in
                    if index > 0 {
                        Text("|")
                            .foregroundStyle(.gray)
                    }
                    Text("\(window.usedPercent)%")
                        .foregroundStyle(UsageColor.color(for: window.usedPercent))
                        .monospacedDigit()
                }
                if let constrained = snapshot.windows.prefix(2).max(by: {
                    $0.usedPercent < $1.usedPercent
                }) {
                    Text("⟳\(UsageFormatting.countdown(to: constrained.resetsAt))")
                        .foregroundStyle(.secondary)
                        .monospacedDigit()
                }
            }
        }
        .font(.system(size: 11, weight: .medium, design: .monospaced))
    }
}

struct MenuContentView: View {
    @ObservedObject var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let error = model.errorMessage, model.snapshots.isEmpty {
                Text(error)
                    .font(.callout)
                    .foregroundStyle(.red)
                    .padding()
            }

            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {
                    ForEach(Array(model.dropdownSnapshots.enumerated()), id: \.element.id) { index, snapshot in
                        if index > 0 {
                            Divider()
                                .padding(.horizontal, 16)
                        }
                        ProviderSectionView(snapshot: snapshot, model: model)
                    }
                }
            }
            .frame(maxHeight: 620)

            Divider()
            HStack {
                SettingsLink {
                    Text("Display settings…")
                }
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
        ["error", "no_data"].contains(snapshot.status)
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
    @State private var launchError: String?

    var body: some View {
        Form {
            Section("Indicator mode") {
                Picker("Mode", selection: modeBinding) {
                    Text("Auto: recent 7D change").tag(DisplayMode.auto)
                    Text("Custom provider list").tag(DisplayMode.custom)
                }
                .pickerStyle(.radioGroup)
            }

            Section("Providers") {
                Grid(alignment: .leading, horizontalSpacing: 20, verticalSpacing: 9) {
                    GridRow {
                        Text("Provider")
                        Text("Indicator")
                        Text("Dropdown")
                        Text("Order")
                    }
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    Divider()
                        .gridCellColumns(4)
                    ForEach(Array(model.configuration.providerOrder.enumerated()), id: \.element) { index, provider in
                        GridRow {
                            HStack {
                                ProviderIcon(provider: provider, size: 17)
                                Text(ProviderCatalog.label(for: provider))
                            }
                            Toggle("", isOn: providerBinding(provider, keyPath: \.indicatorProviders))
                                .labelsHidden()
                            Toggle("", isOn: providerBinding(provider, keyPath: \.dropdownProviders))
                                .labelsHidden()
                            HStack(spacing: 5) {
                                Button {
                                    model.moveProvider(provider, offset: -1)
                                } label: {
                                    Image(systemName: "chevron.up")
                                }
                                .disabled(index == 0)
                                Button {
                                    model.moveProvider(provider, offset: 1)
                                } label: {
                                    Image(systemName: "chevron.down")
                                }
                                .disabled(index == model.configuration.providerOrder.count - 1)
                            }
                        }
                    }
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
        .frame(width: 560, height: 430)
    }

    private var modeBinding: Binding<DisplayMode> {
        Binding(
            get: { model.configuration.mode },
            set: model.setMode
        )
    }

    private func providerBinding(
        _ provider: String,
        keyPath: KeyPath<DisplayConfiguration, [String]>
    ) -> Binding<Bool> {
        Binding(
            get: { model.configuration[keyPath: keyPath].contains(provider) },
            set: { enabled in
                let currentlyEnabled = model.configuration[keyPath: keyPath].contains(provider)
                guard enabled != currentlyEnabled else { return }
                if keyPath == \DisplayConfiguration.indicatorProviders {
                    model.toggleIndicator(provider)
                } else {
                    model.toggleDropdown(provider)
                }
            }
        )
    }
}
