import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANAGER = ROOT / "manage.sh"
CODEX_INSTALLER = ROOT / "providers/codex/ubuntu-indicator/install.sh"


class ManagerTests(unittest.TestCase):
    def test_standalone_codex_installer_defaults_to_local_source(self):
        installer = CODEX_INSTALLER.read_text(encoding="utf-8")

        self.assertIn("CODEX_RATE_SOURCE=local", installer)
        self.assertNotIn("CODEX_RATE_SOURCE=auto", installer)

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

    def test_apply_manages_collectors_and_starts_one_unified_ui(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env, log = self._environment(root)
            config = root / "providers.env"
            config.write_text(
                "CODEX=true\nCODEX_RATE_SOURCE=local\n"
                "CLAUDE=false\nGROK=yes\nGEMINI=0\n",
                encoding="utf-8",
            )
            env["RATE_LIMIT_INDICATOR_CONFIG"] = str(config)

            subprocess.run([MANAGER, "apply"], env=env, check=True, capture_output=True, text=True)
            commands = log.read_text(encoding="utf-8")

        self.assertIn("--user stop codex-rate-indicator.service", commands)
        self.assertIn("--user stop claude-rate-indicator.service", commands)
        self.assertIn("--user stop grok-rate-indicator.service", commands)
        self.assertIn("--user stop gemini-rate-indicator.service", commands)
        self.assertIn("--user restart rate-limit-indicator.service", commands)
        self.assertIn("--user disable --now codex-rate-wham-poll.timer", commands)
        self.assertIn("--user disable --now gemini-rate-poll.timer", commands)

    def test_apply_enables_codex_wham_timer_only_after_opt_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env, log = self._environment(root)
            config = root / "providers.env"
            config.write_text(
                "CODEX=true\nCODEX_RATE_SOURCE=wham\n"
                "CLAUDE=false\nGROK=false\nGEMINI=false\n",
                encoding="utf-8",
            )
            env["RATE_LIMIT_INDICATOR_CONFIG"] = str(config)

            subprocess.run(
                [MANAGER, "apply"],
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            commands = log.read_text(encoding="utf-8")

        self.assertIn("--user enable --now codex-rate-wham-poll.timer", commands)

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
            self.assertIn("CODEX_RATE_SOURCE=local", config.read_text(encoding="utf-8"))
            self.assertIn("DISPLAY_MODE=auto", config.read_text(encoding="utf-8"))
            self.assertIn(
                "DISPLAY_PROVIDERS=codex,claude,grok,gemini",
                config.read_text(encoding="utf-8"),
            )
            self.assertIn(
                "DROPDOWN_PROVIDERS=codex,claude,grok,gemini",
                config.read_text(encoding="utf-8"),
            )
            self.assertIn(
                "PROVIDER_ORDER=codex,claude,grok,gemini",
                config.read_text(encoding="utf-8"),
            )
            self.assertNotIn("DISPLAY_PROVIDER=", config.read_text(encoding="utf-8"))
            self.assertIn(f"Exec={launcher} start", unified.read_text(encoding="utf-8"))
            self.assertIn(
                "X-GNOME-Autostart-enabled=false",
                individual.read_text(encoding="utf-8"),
            )

    def test_install_preserves_existing_codex_wham_opt_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env, _ = self._environment(root)
            config = root / ".config/rate-limit-indicator/providers.env"
            config.parent.mkdir(parents=True)
            config.write_text(
                "CODEX=true\nCLAUDE=false\nGROK=false\nGEMINI=false\n",
                encoding="utf-8",
            )
            legacy = root / ".config/codex-rate-indicator/wham.env"
            legacy.parent.mkdir(parents=True)
            legacy.write_text("CODEX_RATE_SOURCE=auto\n", encoding="utf-8")

            subprocess.run(
                [MANAGER, "install"],
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertIn(
                "CODEX_RATE_SOURCE=auto",
                config.read_text(encoding="utf-8"),
            )

    def test_install_imports_codex_wham_opt_in_into_new_shared_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env, _ = self._environment(root)
            legacy = root / ".config/codex-rate-indicator/wham.env"
            legacy.parent.mkdir(parents=True)
            legacy.write_text("CODEX_RATE_SOURCE=wham\n", encoding="utf-8")

            subprocess.run(
                [MANAGER, "install"],
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )

            config = root / ".config/rate-limit-indicator/providers.env"
            self.assertIn(
                "CODEX_RATE_SOURCE=wham",
                config.read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
