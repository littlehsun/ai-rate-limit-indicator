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


if __name__ == "__main__":
    unittest.main()
