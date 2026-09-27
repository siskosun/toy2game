from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import tomllib
import unittest
from unittest import mock

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
ROOT = TOOLS_DIR.parents[1]
sys.path.insert(0, str(TOOLS_DIR))

from install_harnesses import HarnessInstallError, HarnessInstaller


class HarnessInstallerTests(unittest.TestCase):
    def test_installs_one_runtime_and_three_harness_configs(self):
        with tempfile.TemporaryDirectory() as td:
            home = pathlib.Path(td)

            codex = home / ".codex" / "config.toml"
            codex.parent.mkdir(parents=True)
            codex.write_text(
                'model = "gpt-test"\n\n'
                '[mcp_servers.other]\n'
                'command = "other"\n\n'
                '[mcp_servers.game-exp]\n'
                'command = "old"\n'
                'args = ["old.py"]\n',
                encoding="utf-8",
            )

            qoder = home / ".qoder" / "settings.json"
            qoder.parent.mkdir(parents=True)
            qoder.write_text(
                json.dumps(
                    {
                        "theme": "dark",
                        "mcpServers": {
                            "other": {"type": "stdio", "command": "other"}
                        },
                    }
                ),
                encoding="utf-8",
            )

            cursor = home / ".cursor" / "mcp.json"
            cursor.parent.mkdir(parents=True)
            cursor.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "other": {"type": "stdio", "command": "other"}
                        }
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch("install_harnesses.shutil.which", return_value="uv"):
                result = HarnessInstaller(ROOT, home).install()

            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["version"], "0.16.3")
            self.assertEqual(result["repo_binding"], "dynamic")

            runtime = home / ".agents" / "tools" / "game-exp"
            self.assertEqual(
                (runtime / "VERSION.txt").read_text(encoding="utf-8").strip(),
                "0.16.3",
            )
            self.assertTrue(
                (runtime / "tools" / "game-exp" / "mcp_server.py").is_file()
            )
            self.assertTrue(
                (runtime / "tools" / "game-exp" / "install_harnesses.py").is_file()
            )
            icon = (
                runtime
                / "plugins"
                / "game-exp"
                / "skills"
                / "game-exp"
                / "assets"
                / "icon.svg"
            ).read_bytes()
            self.assertGreater(len(icon), 64)
            self.assertIn(b"<svg", icon.lower())

            codex_data = tomllib.loads(codex.read_text(encoding="utf-8"))
            self.assertEqual(codex_data["model"], "gpt-test")
            self.assertIn("other", codex_data["mcp_servers"])
            game_exp = codex_data["mcp_servers"]["game-exp"]
            self.assertEqual(game_exp["command"], "uv")
            self.assertTrue(game_exp["enabled"])
            self.assertEqual(
                pathlib.Path(game_exp["args"][-1]).resolve(),
                (runtime / "tools" / "game-exp" / "mcp_server.py").resolve(),
            )
            self.assertNotIn("GAME_EXP_REPO", codex.read_text(encoding="utf-8"))

            qoder_data = json.loads(qoder.read_text(encoding="utf-8"))
            self.assertEqual(qoder_data["theme"], "dark")
            self.assertIn("other", qoder_data["mcpServers"])
            self.assertEqual(qoder_data["mcpServers"]["game-exp"]["command"], "uv")

            cursor_data = json.loads(cursor.read_text(encoding="utf-8"))
            self.assertIn("other", cursor_data["mcpServers"])
            self.assertEqual(cursor_data["mcpServers"]["game-exp"]["command"], "uv")

            shared_skill = home / ".agents" / "skills" / "game-exp"
            qoder_skill = home / ".qoder" / "skills" / "game-exp"
            self.assertTrue((shared_skill / "SKILL.md").is_file())
            self.assertTrue((qoder_skill / "SKILL.md").is_file())
            self.assertEqual(
                pathlib.Path(result["skills"]["cursor"]).resolve(),
                shared_skill.resolve(),
            )
            self.assertEqual(
                pathlib.Path(result["skills"]["codex"]).resolve(),
                shared_skill.resolve(),
            )

    def test_runtime_install_falls_back_when_directory_swap_is_locked(self):
        with tempfile.TemporaryDirectory() as td:
            home = pathlib.Path(td)
            installer = HarnessInstaller(ROOT, home)
            original_replace = __import__("install_harnesses")._atomic_replace_dir
            calls = {"count": 0}

            def locked_once(source, target):
                calls["count"] += 1
                if target == installer.runtime_dir:
                    raise PermissionError("simulated live Windows directory lock")
                return original_replace(source, target)

            with (
                mock.patch("install_harnesses.shutil.which", return_value="uv"),
                mock.patch("install_harnesses._atomic_replace_dir", side_effect=locked_once),
            ):
                result = installer.install()

            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["runtime_update_mode"], "filewise-fallback")
            self.assertEqual(
                (home / ".agents" / "tools" / "game-exp" / "VERSION.txt")
                .read_text(encoding="utf-8")
                .strip(),
                "0.16.3",
            )
            self.assertTrue(
                (
                    home
                    / ".agents"
                    / "tools"
                    / "game-exp"
                    / "tools"
                    / "game-exp"
                    / "mcp_server.py"
                ).is_file()
            )

    def test_runtime_install_handles_file_locked_against_replace(self):
        with tempfile.TemporaryDirectory() as td:
            home = pathlib.Path(td)
            installer = HarnessInstaller(ROOT, home)
            module = __import__("install_harnesses")
            original_replace = module._atomic_replace_dir
            original_atomic_write = module._atomic_write
            locked_path = installer.runtime_dir / "tools" / "game-exp" / "project_setup.py"
            state = {"raised": False}

            with mock.patch("install_harnesses.shutil.which", return_value="uv"):
                installer.install()
            self.assertTrue(locked_path.exists())

            def locked_directory(source, target):
                if target == installer.runtime_dir:
                    raise PermissionError("simulated live runtime directory lock")
                return original_replace(source, target)

            def locked_file(path, content):
                if path == locked_path and path.exists() and not state["raised"]:
                    state["raised"] = True
                    raise PermissionError("simulated reader denying delete-sharing")
                return original_atomic_write(path, content)

            with (
                mock.patch("install_harnesses.shutil.which", return_value="uv"),
                mock.patch("install_harnesses._atomic_replace_dir", side_effect=locked_directory),
                mock.patch("install_harnesses._atomic_write", side_effect=locked_file),
            ):
                result = installer.install()

            self.assertTrue(state["raised"])
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["runtime_update_mode"], "filewise-live-fallback")
            self.assertEqual(
                (home / ".agents" / "tools" / "game-exp" / "VERSION.txt")
                .read_text(encoding="utf-8")
                .strip(),
                "0.16.3",
            )
            self.assertEqual(
                locked_path.read_bytes(),
                (ROOT / "tools" / "game-exp" / "project_setup.py").read_bytes(),
            )
            self.assertEqual(
                list(locked_path.parent.glob("project_setup.py.live-backup-*")),
                [],
            )

    def test_reinstall_is_idempotent_at_config_semantics(self):
        with tempfile.TemporaryDirectory() as td:
            home = pathlib.Path(td)
            with mock.patch("install_harnesses.shutil.which", return_value="uv"):
                first = HarnessInstaller(ROOT, home).install()
                second = HarnessInstaller(ROOT, home).install()

            self.assertEqual(first["version"], second["version"])
            codex = tomllib.loads(
                (home / ".codex" / "config.toml").read_text(encoding="utf-8")
            )
            self.assertEqual(
                list(codex["mcp_servers"]).count("game-exp"),
                1,
            )
            qoder = json.loads(
                (home / ".qoder" / "settings.json").read_text(encoding="utf-8")
            )
            cursor = json.loads(
                (home / ".cursor" / "mcp.json").read_text(encoding="utf-8")
            )
            self.assertEqual(qoder["mcpServers"]["game-exp"], cursor["mcpServers"]["game-exp"])

    def test_invalid_existing_config_fails_before_runtime_write(self):
        with tempfile.TemporaryDirectory() as td:
            home = pathlib.Path(td)
            qoder = home / ".qoder" / "settings.json"
            qoder.parent.mkdir(parents=True)
            qoder.write_text("{not-json", encoding="utf-8")

            with mock.patch("install_harnesses.shutil.which", return_value="uv"):
                with self.assertRaises(HarnessInstallError):
                    HarnessInstaller(ROOT, home).install()

            self.assertFalse((home / ".agents" / "tools" / "game-exp").exists())
            self.assertFalse((home / ".codex" / "config.toml").exists())

    def test_missing_uv_fails_before_install(self):
        with tempfile.TemporaryDirectory() as td:
            home = pathlib.Path(td)
            with mock.patch("install_harnesses.shutil.which", return_value=None):
                with self.assertRaisesRegex(HarnessInstallError, "uv is required"):
                    HarnessInstaller(ROOT, home).install()
            self.assertFalse((home / ".agents" / "tools" / "game-exp").exists())


if __name__ == "__main__":
    unittest.main()
