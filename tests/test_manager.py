import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANAGER = ROOT / "manage.sh"


class ManagerTests(unittest.TestCase):
    def _environment(self, root: Path) -> tuple[dict[str, str], Path]:
        fake_bin = root / "bin"
        fake_bin.mkdir()
        log = root / "systemctl.log"
        systemctl = fake_bin / "systemctl"
        systemctl.write_text(
            "#!/bin/sh\n"
            'printf "%s\\n" "$*" >> "$MANAGER_TEST_LOG"\n'
            "exit 0\n",
            encoding="utf-8",
        )
        systemctl.chmod(0o755)
        env = os.environ.copy()
        env.pop("SUDO_USER", None)
        env.update(
            {
                "HOME": str(root),
                "MANAGER_TEST_LOG": str(log),
                "PATH": f"{fake_bin}:{env['PATH']}",
            }
        )
        return env, log

    def test_apply_starts_enabled_and_stops_disabled_providers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env, log = self._environment(root)
            config = root / "providers.env"
            config.write_text(
                "CODEX=true\nCLAUDE=false\nGROK=yes\nGEMINI=0\n",
                encoding="utf-8",
            )
            env["RATE_LIMIT_INDICATOR_CONFIG"] = str(config)

            subprocess.run([MANAGER, "apply"], env=env, check=True, capture_output=True, text=True)
            commands = log.read_text(encoding="utf-8")

        self.assertIn("--user restart codex-rate-indicator.service", commands)
        self.assertIn("--user stop claude-rate-indicator.service", commands)
        self.assertIn("--user restart grok-rate-indicator.service", commands)
        self.assertIn("--user stop gemini-rate-indicator.service", commands)
        self.assertIn("--user enable --now codex-rate-wham-poll.timer", commands)
        self.assertIn("--user disable --now gemini-rate-poll.timer", commands)

    def test_install_creates_one_autostart_and_disables_provider_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env, _ = self._environment(root)
            individual = root / ".config/autostart/codex-rate-indicator.desktop"
            individual.parent.mkdir(parents=True)
            individual.write_text(
                "[Desktop Entry]\nX-GNOME-Autostart-enabled=true\n",
                encoding="utf-8",
            )

            subprocess.run([MANAGER, "install"], env=env, check=True, capture_output=True, text=True)

            config = root / ".config/rate-limit-indicator/providers.env"
            unified = root / ".config/autostart/rate-limit-indicators.desktop"
            launcher = root / ".local/bin/rate-limit-indicators"

            self.assertTrue(config.is_file())
            self.assertIn("GEMINI=true", config.read_text(encoding="utf-8"))
            self.assertIn(f"Exec={launcher} start", unified.read_text(encoding="utf-8"))
            self.assertIn(
                "X-GNOME-Autostart-enabled=false",
                individual.read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
