import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MACOS = ROOT / "macos"


class UnifiedMacOSTests(unittest.TestCase):
    def test_native_ui_consumes_shared_normalized_cli(self):
        backend = (
            MACOS / "Sources/RateLimitIndicatorMac/BackendClient.swift"
        ).read_text(encoding="utf-8")
        installer = (MACOS / "install.sh").read_text(encoding="utf-8")

        self.assertIn('"--json"', backend)
        self.assertIn('unified-indicator/cli.py', installer)
        self.assertIn('unified-indicator/adapters.py', installer)

    def test_swift_ui_does_not_duplicate_provider_api_endpoints(self):
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (MACOS / "Sources/RateLimitIndicatorMac").glob("*.swift")
        )

        self.assertNotIn("api.anthropic.com", source)
        self.assertNotIn("chatgpt.com/backend-api", source)
        self.assertNotIn("cli-chat-proxy.grok.com", source)
        self.assertNotIn("RetrieveUserQuotaSummary", source)

    def test_package_and_installer_target_unified_macos_app(self):
        package = (MACOS / "Package.swift").read_text(encoding="utf-8")
        root_installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        mac_installer = (MACOS / "install.sh").read_text(encoding="utf-8")

        self.assertIn(".macOS(.v14)", package)
        self.assertIn("RateLimitIndicatorMac", package)
        self.assertIn("macos|codex-macos", root_installer)
        self.assertIn("LSUIElement", mac_installer)
        self.assertIn("poll-provider.sh", mac_installer)
        self.assertIn("com.hsun.codex-rate-menubar", mac_installer)
        self.assertIn(
            'launchctl bootout "gui/$UID/com.hsun.codex-rate-menubar"',
            mac_installer,
        )
        self.assertIn('rm -f "$LEGACY_CODEX_PLIST"', mac_installer)
        self.assertIn("RateLimitIndicatorConfigPath", mac_installer)
        self.assertIn("EnvironmentVariables.RATE_LIMIT_INDICATOR_CONFIG", mac_installer)
        self.assertIn("os.path.realpath(os.path.expanduser(sys.argv[1]))", mac_installer)
        self.assertIn('config_dir_created=false', mac_installer)
        self.assertIn(
            '"$config_dir_created" == true && "$CONFIG_FILE" == "$DEFAULT_CONFIG_FILE"',
            mac_installer,
        )
        self.assertIn(
            '"$config_file_created" == true || "$CONFIG_FILE" == "$DEFAULT_CONFIG_FILE"',
            mac_installer,
        )
        self.assertIn("legacy_login_was_enabled=true", mac_installer)
        self.assertIn("migrate-legacy-launch-at-login", mac_installer)
        self.assertLess(
            mac_installer.index('launchctl bootstrap "gui/$UID" "$plist"'),
            mac_installer.index('rm -f "$LEGACY_CODEX_PLIST"'),
        )

    def test_refresh_failure_marks_retained_snapshots_stale(self):
        app_model = (
            MACOS / "Sources/RateLimitIndicatorMac/AppModel.swift"
        ).read_text(encoding="utf-8")
        backend = (
            MACOS / "Sources/RateLimitIndicatorMac/BackendClient.swift"
        ).read_text(encoding="utf-8")

        self.assertIn("snapshots.map { $0.markingStale() }", app_model)
        self.assertIn(
            'environment["RATE_LIMIT_INDICATOR_CONFIG"] = configURL.path',
            backend,
        )

    def test_settings_surface_save_errors_and_auto_click_selects_provider(self):
        app_model = (
            MACOS / "Sources/RateLimitIndicatorMac/AppModel.swift"
        ).read_text(encoding="utf-8")
        views = (
            MACOS / "Sources/RateLimitIndicatorMac/Views.swift"
        ).read_text(encoding="utf-8")

        self.assertIn("configuration.indicatorProviders = [provider]", app_model)
        self.assertIn("configuration = previous", app_model)
        self.assertIn("model.configurationErrorMessage", views)
        self.assertIn("model.configuration.enabledProviderOrder", views)
        self.assertIn("reloadConfiguration()", app_model)

    def test_menu_panel_has_intrinsic_content_and_recoverable_empty_state(self):
        views = (
            MACOS / "Sources/RateLimitIndicatorMac/Views.swift"
        ).read_text(encoding="utf-8")

        self.assertNotIn("LazyVStack", views)
        self.assertIn(".frame(height: providerListHeight)", views)
        self.assertIn("No providers shown in the menu panel", views)
        self.assertIn("Open Display settings…", views)

    def test_menu_panel_activates_app_before_opening_settings(self):
        views = (
            MACOS / "Sources/RateLimitIndicatorMac/Views.swift"
        ).read_text(encoding="utf-8")

        self.assertIn(r"@Environment(\.openSettings)", views)
        self.assertIn("NSApp.activate()", views)
        self.assertIn("openSettings()", views)

    def test_multiple_menu_bar_providers_use_one_composite_brand_image(self):
        views = (
            MACOS / "Sources/RateLimitIndicatorMac/Views.swift"
        ).read_text(encoding="utf-8")

        self.assertIn("private enum MenuBarCompositeImage", views)
        self.assertIn("MenuBarCompositeImage.make(for: snapshots)", views)
        self.assertIn("logo.draw(", views)
        self.assertIn("NSColor.labelColor.withAlphaComponent(0.65)", views)
        self.assertIn("snapshot.windows.first(where: \\.isSevenDay)", views)
        self.assertIn("snapshot.indicatorResetWindow", views)
        self.assertIn('snapshot.status == "stale" ? "~" : ""', views)


if __name__ == "__main__":
    unittest.main()
