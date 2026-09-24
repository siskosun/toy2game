from __future__ import annotations

import json
import re
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
PLUGIN = ROOT / "plugins" / "game-exp"
SKILL = PLUGIN / "skills" / "game-exp" / "SKILL.md"


class GameExpSkillContractTests(unittest.TestCase):
    def test_portable_plugin_manifest(self):
        manifest = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "game-exp")
        self.assertEqual(manifest["version"], "0.1.0")
        self.assertEqual(
            manifest["$schema"],
            "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        )
        self.assertTrue(SKILL.exists())

    def test_repo_marketplace_points_to_plugin(self):
        market = json.loads(
            (ROOT / ".agents" / "plugins" / "marketplace.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(market["name"], "toy2game-local")
        entry = next(row for row in market["plugins"] if row["name"] == "game-exp")
        self.assertEqual(entry["source"]["source"], "local")
        self.assertEqual(entry["source"]["path"], "./plugins/game-exp")
        self.assertIn(
            entry["policy"]["installation"],
            {"AVAILABLE", "INSTALLED_BY_DEFAULT"},
        )

    def test_codex_project_config_enables_mcp_and_plugin(self):
        config = tomllib.loads((ROOT / ".codex" / "config.toml").read_text(encoding="utf-8"))
        server = config["mcp_servers"]["game-exp"]
        self.assertEqual(server["command"], "uv")
        self.assertIn("tools/game-exp/mcp_server.py", server["args"])
        self.assertEqual(server["env"]["GAME_EXP_REPO"], "siskosun/toy2game")
        self.assertTrue(config["plugins"]["game-exp@toy2game-local"]["enabled"])

    def test_skill_frontmatter_and_domain_tools(self):
        content = SKILL.read_text(encoding="utf-8")
        self.assertRegex(content, r"(?s)^---\nname: game-exp\ndescription: .+?\n---")
        required_tools = {
            "game_exp_status",
            "game_exp_board",
            "game_exp_doctor",
            "game_exp_experiment_get",
            "game_exp_experiment_bind",
            "game_exp_initialize",
            "game_exp_candidate_build",
            "game_exp_review_record",
            "game_exp_decision_submit",
            "game_exp_rehearse",
            "game_exp_integrate",
            "game_exp_integrate_finalize",
            "game_exp_archive",
            "game_exp_archive_abort",
            "game_exp_request_get",
        }
        for name in required_tools:
            self.assertIn(name, content)

    def test_skill_preserves_human_gates_and_async_semantics(self):
        content = SKILL.read_text(encoding="utf-8")
        for phrase in (
            "Never auto-approve a human gate",
            "Never report `ACCEPTED` as completion",
            "PASS Review does not automatically mean PROMISING",
            "only after the user explicitly chooses/selects the Candidate",
            "ATOMIC_DELETE",
            "RETAIN_BRANCH",
            "Archive is a destructive/recovery-sensitive workflow",
        ):
            self.assertIn(phrase, content)
        self.assertIn("UNKNOWN", content)
        self.assertIn("CONFLICT", content)
        self.assertIn("REJECTED", content)

    def test_skill_has_codex_board_entrypoint(self):
        content = SKILL.read_text(encoding="utf-8")
        for phrase in (
            "## Codex Board",
            "game_exp_board",
            "read-only projection",
            "Next gate",
            "persistent graphical MCP Apps panel",
        ):
            self.assertIn(phrase, content)

    def test_workflow_reference_maps_board_tool(self):
        content = (PLUGIN / "skills" / "game-exp" / "references" / "workflow.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("Open experiment Board / panel", content)
        self.assertIn("game_exp_board", content)

    def test_plugin_contains_exactly_one_skill_entrypoint(self):
        entrypoints = list(PLUGIN.glob("skills/**/SKILL.md"))
        self.assertEqual(entrypoints, [SKILL])


if __name__ == "__main__":
    unittest.main()
