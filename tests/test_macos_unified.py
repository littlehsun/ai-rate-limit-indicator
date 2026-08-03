import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MACOS = ROOT / "macos"


class UnifiedMacOSTests(unittest.TestCase):
    def test_codex_poller_requires_network_source_opt_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = Path(tmp)
            config = temp_dir / "providers.env"
            marker = temp_dir / "python-ran"
            fake_python = temp_dir / "python3"
            wham_env = temp_dir / "wham.env"
            config.write_text(
                "CODEX=true\nCODEX_RATE_SOURCE=local\n",
                encoding="utf-8",
            )
            fake_python.write_text(
                '#!/usr/bin/env bash\nprintf "%s" "${CHATGPT_ACCESS_TOKEN:-}" > "$MARKER"\n',
                encoding="utf-8",
            )
            wham_env.write_text(
                "export CHATGPT_ACCESS_TOKEN=test-token\n",
                encoding="utf-8",
            )
            fake_python.chmod(0o700)
            environment = os.environ.copy()
            environment.update(
                {
                    "MARKER": str(marker),
                    "RATE_LIMIT_INDICATOR_CONFIG": str(config),
                    "RATE_LIMIT_INDICATOR_PYTHON": str(fake_python),
                    "RATE_LIMIT_INDICATOR_APP_SUPPORT": str(temp_dir),
                    "CODEX_RATE_WHAM_ENV": str(wham_env),
                }
            )

            local_result = subprocess.run(
                ["bash", str(MACOS / "poll-provider.sh"), "codex"],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(local_result.returncode, 0, local_result.stderr)
            self.assertFalse(marker.exists())

            config.write_text(
                "CODEX=true\nCODEX_RATE_SOURCE=auto\n",
                encoding="utf-8",
            )
            opted_in_result = subprocess.run(
                ["bash", str(MACOS / "poll-provider.sh"), "codex"],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(opted_in_result.returncode, 0, opted_in_result.stderr)
            self.assertTrue(marker.exists())
            self.assertEqual(marker.read_text(encoding="utf-8"), "test-token")

    def test_provider_pollers_accept_quoted_enabled_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = Path(tmp)
            config = temp_dir / "providers.env"
            marker = temp_dir / "python-ran"
            fake_python = temp_dir / "python3"
            config.write_text(
                "CODEX='true'\nCODEX_RATE_SOURCE=\"auto\"\nGROK=\"yes\"\n",
                encoding="utf-8",
            )
            fake_python.write_text(
                '#!/usr/bin/env bash\nprintf "%s\n" "$*" >> "$MARKER"\n',
                encoding="utf-8",
            )
            fake_python.chmod(0o700)
            environment = os.environ.copy()
            environment.update(
                {
                    "MARKER": str(marker),
                    "RATE_LIMIT_INDICATOR_CONFIG": str(config),
                    "RATE_LIMIT_INDICATOR_PYTHON": str(fake_python),
                    "RATE_LIMIT_INDICATOR_APP_SUPPORT": str(temp_dir),
                    "CODEX_RATE_WHAM_ENV": str(temp_dir / "missing-wham.env"),
                }
            )

            for provider in ("codex", "grok"):
                result = subprocess.run(
                    ["bash", str(MACOS / "poll-provider.sh"), provider],
                    check=False,
                    capture_output=True,
                    text=True,
                    env=environment,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

            invocations = marker.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(invocations), 2)
            self.assertTrue(invocations[0].endswith("collectors/wham.py --once"))
            self.assertTrue(invocations[1].endswith("collectors/grok_rate.py --once"))

    def test_native_ui_consumes_shared_normalized_cli(self):
        backend = (
            MACOS / "Sources/RateLimitIndicatorMac/BackendClient.swift"
        ).read_text(encoding="utf-8")
        installer = (MACOS / "install.sh").read_text(encoding="utf-8")
        launch_manager = (
            MACOS / "Sources/RateLimitIndicatorMac/LaunchAtLoginManager.swift"
        ).read_text(encoding="utf-8")

        self.assertIn('"--json"', backend)
        self.assertIn('unified-indicator/cli.py', installer)
        self.assertIn('unified-indicator/adapters.py', installer)
        self.assertLess(
            backend.index("let outputReader = Task.detached"),
            backend.index("process.waitUntilExit()"),
        )
        self.assertIn("retireLegacyAgent()", launch_manager)
        self.assertIn("com.hsun.codex-rate-menubar.plist", launch_manager)
        self.assertIn("migrationErrorMessage", launch_manager)
        self.assertIn("case .requiresApproval:", launch_manager)

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
        self.assertIn("RateLimitIndicatorPythonPath", mac_installer)
        self.assertIn("RateLimitIndicatorCodexHome", mac_installer)
        self.assertIn("RateLimitIndicatorClaudeConfigDir", mac_installer)
        self.assertIn("RateLimitIndicatorClaudeOAuthCredentialsFile", mac_installer)
        self.assertIn("RateLimitIndicatorGrokHome", mac_installer)
        self.assertIn("RateLimitIndicatorGrokRateCache", mac_installer)
        self.assertIn("EnvironmentVariables.RATE_LIMIT_INDICATOR_CONFIG", mac_installer)
        self.assertIn("EnvironmentVariables.RATE_LIMIT_INDICATOR_PYTHON", mac_installer)
        self.assertIn("EnvironmentVariables.GROK_HOME", mac_installer)
        self.assertIn("EnvironmentVariables.GROK_RATE_CACHE", mac_installer)
        self.assertIn("EnvironmentVariables.GROK_RATE_BILLING_URL", mac_installer)
        self.assertIn('*) legacy_codex_source=local ;;', mac_installer)
        self.assertIn('echo "CODEX_RATE_SOURCE=$legacy_codex_source"', mac_installer)
        self.assertIn("LEGACY_CODEX_ENV", mac_installer)
        self.assertIn('auto|wham) ;;', mac_installer)
        self.assertIn("CODEX_RATE_SOURCE[[:space:]]*=", mac_installer)
        self.assertIn("(export[[:space:]]+)?CODEX_RATE_SOURCE", mac_installer)
        self.assertIn("os.path.realpath(os.path.expanduser(sys.argv[1]))", mac_installer)
        self.assertIn('APP_DIR="$(canonicalize_path "$APP_DIR")"', mac_installer)
        self.assertLess(
            mac_installer.index('APP_DIR="$(canonicalize_path "$APP_DIR")"'),
            mac_installer.index('APP_EXECUTABLE="$APP_DIR/Contents/MacOS/RateLimitIndicatorMac"'),
        )
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
        self.assertIn("STAGED_APP_EXECUTABLE", mac_installer)
        self.assertIn('ps -U "$UID" -ww -o pid=', mac_installer)
        self.assertIn('[[ "$command" == "$APP_EXECUTABLE" ]]', mac_installer)
        self.assertIn('done < <(app_process_ids)', mac_installer)
        self.assertNotIn('pgrep -u "$UID"', mac_installer)
        self.assertNotIn('pkill -u "$UID"', mac_installer)
        self.assertIn('mv -f "$STAGED_APP_EXECUTABLE" "$APP_EXECUTABLE"', mac_installer)
        self.assertIn('if [[ "$app_was_running" == true ]]', mac_installer)
        self.assertLess(
            mac_installer.index('launchctl bootstrap "gui/$UID" "$plist"'),
            mac_installer.index('mv -f "$STAGED_APP_EXECUTABLE" "$APP_EXECUTABLE"'),
        )
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
        self.assertIn('environment["CODEX_HOME"] = codexHome', backend)
        self.assertIn('environment["CLAUDE_CONFIG_DIR"] = claudeConfigDir', backend)
        self.assertIn(
            'environment["CLAUDE_OAUTH_CREDENTIALS_FILE"] = claudeCredentials',
            backend,
        )
        self.assertIn('environment["GROK_HOME"] = grokHome', backend)
        self.assertIn('environment["GROK_RATE_CACHE"] = grokRateCache', backend)

    def test_settings_surface_save_errors_and_auto_click_selects_provider(self):
        app_model = (
            MACOS / "Sources/RateLimitIndicatorMac/AppModel.swift"
        ).read_text(encoding="utf-8")
        views = (
            MACOS / "Sources/RateLimitIndicatorMac/Views.swift"
        ).read_text(encoding="utf-8")
        configuration = (
            MACOS / "Sources/RateLimitIndicatorMac/Configuration.swift"
        ).read_text(encoding="utf-8")

        self.assertIn("configuration.indicatorProviders = [provider]", app_model)
        self.assertIn("configuration = previous", app_model)
        self.assertIn("model.configurationErrorMessage", views)
        self.assertIn("model.configuration.enabledProviderOrder", views)
        self.assertIn("reloadConfiguration()", app_model)
        self.assertIn("try existingContents(at: url)", configuration)
        self.assertNotIn("let existing = (try?", configuration)
        self.assertIn("return .noEnabledProviders", configuration)

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
        self.assertIn(".filter(\\.isSevenDay)", views)
        self.assertIn(".max(by: { $0.usedPercent < $1.usedPercent })", views)
        self.assertIn("snapshot.indicatorResetWindow", views)
        self.assertIn("snapshot.indicatorDisplayWindows", views)
        self.assertIn('snapshot.status == "stale" ? "~" : ""', views)
        self.assertIn('snapshot.status == "stale" ? "~" : ""', views)


if __name__ == "__main__":
    unittest.main()
