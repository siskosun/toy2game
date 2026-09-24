from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS_DIR))

from bootstrap import (
    BootstrapError,
    Bootstrapper,
    NODE_NPM_POLICY,
    _managed_paths,
)


class BootstrapTests(unittest.TestCase):
    def make_source(self, root: pathlib.Path) -> None:
        for rel in _managed_paths():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            if rel == "plugins/game-exp/plugin.json":
                path.write_text(
                    json.dumps({"name": "game-exp", "repository": "old"}),
                    encoding="utf-8",
                )
            else:
                path.write_text(f"source:{rel}\n", encoding="utf-8")

    def make_target(self, root: pathlib.Path) -> None:
        (root / ".git").mkdir(parents=True)
    def test_plan_generates_repo_specific_config(self):
        with tempfile.TemporaryDirectory() as sd, tempfile.TemporaryDirectory() as td:
            source, target = pathlib.Path(sd), pathlib.Path(td)
            self.make_source(source)
            self.make_target(target)
            plan = Bootstrapper(source, target, "acme/game").plan()
            by_rel = {p.path.relative_to(target).as_posix(): p.content for p in plan}
            config = by_rel[".codex/config.toml"].decode()
            self.assertIn('GAME_EXP_REPO = "acme/game"', config)
            plugin = json.loads(by_rel["plugins/game-exp/plugin.json"])
            self.assertEqual(plugin["repository"], "https://github.com/acme/game")
            policy = json.loads(by_rel[".game-exp/project-policy.json"])
            self.assertEqual(policy["adapter"], "node-npm")

    def test_install_preserves_other_marketplace_plugins(self):
        with tempfile.TemporaryDirectory() as sd, tempfile.TemporaryDirectory() as td:
            source, target = pathlib.Path(sd), pathlib.Path(td)
            self.make_source(source)
            self.make_target(target)
            market = target / ".agents/plugins/marketplace.json"
            market.parent.mkdir(parents=True)
            market.write_text(
                json.dumps({"name": "existing-local", "plugins": [{"name": "other"}]}),
                encoding="utf-8",
            )
            Bootstrapper(source, target, "acme/game").install()
            data = json.loads(market.read_text(encoding="utf-8"))
            self.assertEqual([x["name"] for x in data["plugins"]], ["other", "game-exp"])
            config = (target / ".codex/config.toml").read_text(encoding="utf-8")
            self.assertIn('game-exp@existing-local', config)
    def test_managed_conflict_fails_before_writes(self):
        with tempfile.TemporaryDirectory() as sd, tempfile.TemporaryDirectory() as td:
            source, target = pathlib.Path(sd), pathlib.Path(td)
            self.make_source(source)
            self.make_target(target)
            conflict = target / "tools/game-exp/client.py"
            conflict.parent.mkdir(parents=True)
            conflict.write_text("local changes", encoding="utf-8")
            with self.assertRaises(BootstrapError):
                Bootstrapper(source, target, "acme/game").install()
            self.assertFalse((target / ".codex/config.toml").exists())

    def test_existing_valid_policy_is_preserved(self):
        with tempfile.TemporaryDirectory() as sd, tempfile.TemporaryDirectory() as td:
            source, target = pathlib.Path(sd), pathlib.Path(td)
            self.make_source(source)
            self.make_target(target)
            policy = json.loads(json.dumps(NODE_NPM_POLICY))
            policy["candidate"]["required_paths"] = ["custom/index.html"]
            path = target / ".game-exp/project-policy.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(policy), encoding="utf-8")
            Bootstrapper(source, target, "acme/game").install()
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), policy)

    def test_codex_collision_fails_closed(self):
        with tempfile.TemporaryDirectory() as sd, tempfile.TemporaryDirectory() as td:
            source, target = pathlib.Path(sd), pathlib.Path(td)
            self.make_source(source)
            self.make_target(target)
            config = target / ".codex/config.toml"
            config.parent.mkdir(parents=True)
            config.write_text("[mcp_servers.game-exp]\ncommand='other'\n", encoding="utf-8")
            with self.assertRaises(BootstrapError):
                Bootstrapper(source, target, "acme/game").plan()
    def test_requires_git_worktree_and_distinct_source(self):
        with tempfile.TemporaryDirectory() as sd, tempfile.TemporaryDirectory() as td:
            source, target = pathlib.Path(sd), pathlib.Path(td)
            self.make_source(source)
            with self.assertRaises(BootstrapError):
                Bootstrapper(source, target, "acme/game").plan()
            self.make_target(source)
            with self.assertRaises(BootstrapError):
                Bootstrapper(source, source, "acme/game").plan()

    def test_rejects_invalid_repo_name(self):
        with tempfile.TemporaryDirectory() as sd, tempfile.TemporaryDirectory() as td:
            with self.assertRaises(BootstrapError):
                Bootstrapper(pathlib.Path(sd), pathlib.Path(td), "not-a-repo")


if __name__ == "__main__":
    unittest.main()