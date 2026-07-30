import Combine
import Foundation

@MainActor
final class AppModel: ObservableObject {
    @Published private(set) var snapshots: [ProviderSnapshot] = []
    @Published private(set) var indicatorSnapshots: [ProviderSnapshot] = []
    @Published private(set) var isRefreshing = false
    @Published private(set) var errorMessage: String?
    @Published var configuration: DisplayConfiguration

    private let backend: BackendClient
    private let autoSelector = AutoDisplaySelector()

    init(
        backend: BackendClient = BackendClient(),
        configuration: DisplayConfiguration = ConfigurationStore.load()
    ) {
        self.backend = backend
        self.configuration = configuration
        Task { [weak self] in
            await self?.refreshLoop()
        }
    }

    var dropdownSnapshots: [ProviderSnapshot] {
        let byID = Dictionary(uniqueKeysWithValues: snapshots.map { ($0.provider, $0) })
        return configuration.providerOrder.compactMap { provider in
            guard configuration.dropdownProviders.contains(provider) else { return nil }
            return byID[provider]
        }
    }

    func refresh() async {
        guard !isRefreshing else { return }
        isRefreshing = true
        defer { isRefreshing = false }
        do {
            snapshots = try await backend.fetchSnapshots()
            errorMessage = nil
            resolveIndicatorSnapshots()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func refreshLoop() async {
        while !Task.isCancelled {
            await refresh()
            try? await Task.sleep(nanoseconds: 60_000_000_000)
        }
    }

    func selectProviderFromDropdown(_ provider: String) {
        if configuration.mode == .auto {
            configuration.mode = .custom
            configuration.indicatorProviders = [provider]
        } else if configuration.indicatorProviders.contains(provider) {
            configuration.indicatorProviders.removeAll { $0 == provider }
        } else {
            configuration.indicatorProviders.append(provider)
        }
        persistConfiguration()
    }

    func setMode(_ mode: DisplayMode) {
        configuration.mode = mode
        persistConfiguration()
    }

    func toggleIndicator(_ provider: String) {
        toggle(provider, in: &configuration.indicatorProviders)
        persistConfiguration()
    }

    func toggleDropdown(_ provider: String) {
        toggle(provider, in: &configuration.dropdownProviders)
        persistConfiguration()
    }

    func moveProvider(_ provider: String, offset: Int) {
        guard let source = configuration.providerOrder.firstIndex(of: provider) else { return }
        let destination = source + offset
        guard configuration.providerOrder.indices.contains(destination) else { return }
        configuration.providerOrder.swapAt(source, destination)
        persistConfiguration()
    }

    func isSelectedForIndicator(_ provider: String) -> Bool {
        configuration.mode == .custom && configuration.indicatorProviders.contains(provider)
    }

    private func toggle(_ provider: String, in providers: inout [String]) {
        if providers.contains(provider) {
            providers.removeAll { $0 == provider }
        } else {
            providers.append(provider)
        }
    }

    private func persistConfiguration() {
        do {
            try ConfigurationStore.save(configuration)
            errorMessage = nil
        } catch {
            errorMessage = "Could not save display settings: \(error.localizedDescription)"
        }
        resolveIndicatorSnapshots()
    }

    private func resolveIndicatorSnapshots() {
        if configuration.mode == .auto {
            indicatorSnapshots = autoSelector.choose(from: snapshots).map { [$0] } ?? []
            return
        }
        let selected = Set(configuration.indicatorProviders)
        let byID = Dictionary(uniqueKeysWithValues: snapshots.map { ($0.provider, $0) })
        indicatorSnapshots = configuration.providerOrder.compactMap { provider in
            guard selected.contains(provider) else { return nil }
            return byID[provider]
        }
    }
}
