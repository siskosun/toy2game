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
        self.assertEqual(manifest["version"], "0.14.0")
        self.assertEqual(
            manifest["$schema"],
            "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        )
        self.assertTrue(SKILL.exists())

    def test_skill_icon_is_packaged_and_referenced(self):
        icon = PLUGIN / "skills" / "game-exp" / "assets" / "icon.svg"
        metadata = (PLUGIN / "skills" / "game-exp" / "agents" / "openai.yaml").read_text(
            encoding="utf-8"
        )
        bootstrap = (ROOT / "tools" / "game-exp" / "bootstrap.py").read_text(
            encoding="utf-8"
        )
        self.assertTrue(icon.exists())
        self.assertIn("icon_small: assets/icon.svg", metadata)
        self.assertIn("icon_large: assets/icon.svg", metadata)
        self.assertIn(
            '"plugins/game-exp/skills/game-exp/assets/icon.svg"',
            bootstrap,
        )

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
            "game_exp_project_preflight",
            "game_exp_project_init",
            "game_exp_conformance_suite",
            "game_exp_conformance_start",
            "game_exp_conformance_result",
            "game_exp_conformance_compare",
            "game_exp_status",
            "game_exp_access_check",
            "game_exp_capabilities",
            "game_exp_notifications",
            "game_exp_prototype_handoff",
            "game_exp_board",
            "game_exp_experiment_panel",
            "game_exp_subject_panel",
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
            "game_exp_operation_get",
            "game_exp_operation_resume",
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
        self.assertIn("Keep game-exp orchestration self-contained", content)
        self.assertIn(".ai/HANDOFF.md", content)
        self.assertIn("without `experiment_id`", content)
        self.assertIn("GitHub Bridge", content)
        self.assertIn("claim-without-result", content)
        self.assertIn("authorized GitHub connector", content)

    def test_skill_has_cross_host_board_entrypoint(self):
        content = SKILL.read_text(encoding="utf-8")
        for phrase in (
            "## Experiment Board",
            "game_exp_board",
            "read-only projection",
            "health=FAIL",
            "ChatGPT Work",
            "Codex",
            "host's authorized source-editing capability",
            "总览",
            "待处理",
            "原型",
            "分支图",
            "归档",
            "stable `subject`",
            "system-generated panel entries in Chinese",
            "`relationships`",
            "references/chat-ui.md",
        ):
            self.assertIn(phrase, content)

        board = (
            PLUGIN / "skills" / "game-exp" / "references" / "board.md"
        ).read_text(encoding="utf-8")
        for phrase in (
            "Repository -> Subject/Prototype -> Experiment",
            "需要处理",
            "当前进行",
            "仓库级/未指定原型",
            "禁止继续；重建实验",
            "DO_NOT_USE_RECREATE_EXPERIMENT",
            "views.overview.attention_ids",
            "views.prototypes.groups",
            "views.branches.lanes",
            "views.archive.experiment_ids",
            "views.attention.sections",
            "活动时间线",
            "需要你评审",
            "需要你决策",
            "依赖",
            "阻塞",
            "替代",
            "中文展示约束",
            "聚焦筛选",
            "focus.experiment_ids",
            "attention_only",
            "counts_by_lifecycle",
            "counts_by_health",
            "game_exp_experiment_panel",
            "judgement.success_criteria",
            "game_exp_subject_panel",
            "relationship_edges",
            "发起人",
            "代码贡献者",
            "contributors_complete",
        ):
            self.assertIn(phrase, board)


    def test_workflow_reference_maps_board_tool(self):
        content = (PLUGIN / "skills" / "game-exp" / "references" / "workflow.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("Open experiment Board / panel", content)
        self.assertIn("game_exp_board", content)
        self.assertTrue((PLUGIN / "skills" / "game-exp" / "references" / "github-bridge.md").exists())
        self.assertTrue((PLUGIN / "skills" / "game-exp" / "references" / "board.md").exists())
        self.assertTrue((PLUGIN / "skills" / "game-exp" / "references" / "chat-ui.md").exists())
        self.assertTrue((PLUGIN / "skills" / "game-exp" / "references" / "onboarding.md").exists())
        self.assertTrue((PLUGIN / "skills" / "game-exp" / "references" / "prototype-handoff.md").exists())
        self.assertTrue((PLUGIN / "skills" / "game-exp" / "references" / "exploration-thread.md").exists())
        self.assertTrue((PLUGIN / "skills" / "game-exp" / "references" / "notifications.md").exists())
        self.assertTrue((PLUGIN / "skills" / "game-exp" / "references" / "public-contract.md").exists())
        self.assertTrue((PLUGIN / "skills" / "game-exp" / "references" / "conformance.md").exists())
        self.assertTrue((PLUGIN / "skills" / "game-exp" / "references" / "project-setup.md").exists())

    def test_complete_project_setup_contract_is_fail_closed(self):
        skill = SKILL.read_text(encoding="utf-8")
        setup = (
            PLUGIN / "skills" / "game-exp" / "references" / "project-setup.md"
        ).read_text(encoding="utf-8")
        for phrase in (
            "## Complete project setup",
            "game_exp_project_preflight",
            "game_exp_project_init",
            "PROJECT_READY",
            "Shared/streamable HTTP MCP must not",
        ):
            self.assertIn(phrase, skill)
        for phrase in (
            "PROJECT_READY",
            "RULESETS_PLAN_UNSUPPORTED",
            "Trusted Writer self-test",
            "repo-level `game_exp_doctor` returns `PASS`",
            "never change repository visibility without explicit user approval",
            "There is no \"weak private Free\" compatibility mode",
        ):
            self.assertIn(phrase, setup)

    def test_conformance_contract_is_screening_only(self):
        skill = SKILL.read_text(encoding="utf-8")
        reference = (
            PLUGIN / "skills" / "game-exp" / "references" / "conformance.md"
        ).read_text(encoding="utf-8")
        for phrase in (
            "## Harness conformance screening",
            "eligible_for_real_repo_test=true",
            "game_exp_conformance_suite",
            "game_exp_conformance_start",
            "game_exp_conformance_result",
            "game_exp_conformance_compare",
            "same `suite_digest`",
        ):
            self.assertIn(phrase, skill)
        for phrase in (
            "game-exp-standing-v1",
            "lost-response-recovery",
            "authorization-no-fallback",
            "review-bound-to-candidate",
            "stale-rehearsal-refresh",
            "dependency-review-required",
            "human-gate-preserved",
            "not a substitute for trusted end-to-end validation",
        ):
            self.assertIn(phrase, reference)

    def test_chat_inline_ui_contract_is_read_only_and_has_fallback(self):
        content = (
            PLUGIN / "skills" / "game-exp" / "references" / "chat-ui.md"
        ).read_text(encoding="utf-8")
        for phrase in (
            "仓库总览 -> 原型/主体 -> 单实验",
            "总览",
            "待处理",
            "原型",
            "分支图",
            "归档",
            "local filtering",
            "It must not directly mutate lifecycle state or protected refs",
            "trusted MCP/workflow path",
            "READ_ONLY",
            "发起人",
            "代码贡献者",
            "Text fallback",
        ):
            self.assertIn(phrase, content)

    def test_first_use_onboarding_contract_preserves_trust_and_human_gates(self):
        content = (
            PLUGIN / "skills" / "game-exp" / "references" / "onboarding.md"
        ).read_text(encoding="utf-8")
        for phrase in (
            "连接检查",
            "描述第一个实验",
            "生成实验定义",
            "建立实验",
            "开发与试玩",
            "人工决定",
            "game_exp_access_check",
            "READ_ONLY",
            "WRITE",
            "ADMIN",
            "game_exp_doctor",
            "game_exp_experiment_bind",
            "game_exp_initialize",
            "PASS does not auto-promote",
            "ACCEPTED dispatch",
            "创建第一个实验",
            "跳过新手引导",
        ):
            self.assertIn(phrase, content)

    def test_collaboration_and_prototype_handoff_contracts(self):
        notifications = (
            PLUGIN / "skills" / "game-exp" / "references" / "notifications.md"
        ).read_text(encoding="utf-8")
        exploration = (
            PLUGIN / "skills" / "game-exp" / "references" / "exploration-thread.md"
        ).read_text(encoding="utf-8")
        handoff = (
            PLUGIN / "skills" / "game-exp" / "references" / "prototype-handoff.md"
        ).read_text(encoding="utf-8")
        for phrase in (
            "event_id",
            "external adapter",
            "new experiment",
            "发起人",
        ):
            self.assertIn(phrase, notifications)
        for phrase in (
            "NOT a command to generate multiple variants in parallel",
            "one active experiment",
            "manifest.relationships",
        ):
            self.assertIn(phrase, exploration)
        for phrase in (
            "game_exp_prototype_handoff",
            "Godot Prototype Studio",
            "source SHA",
            "Candidate/Review",
        ):
            self.assertIn(phrase, handoff)

    def test_cross_interface_contract_is_recovery_safe(self):
        skill = SKILL.read_text(encoding="utf-8")
        contract = (
            PLUGIN / "skills" / "game-exp" / "references" / "public-contract.md"
        ).read_text(encoding="utf-8")
        notifications = (
            PLUGIN / "skills" / "game-exp" / "references" / "notifications.md"
        ).read_text(encoding="utf-8")
        handoff = (
            PLUGIN / "skills" / "game-exp" / "references" / "prototype-handoff.md"
        ).read_text(encoding="utf-8")
        board = (
            PLUGIN / "skills" / "game-exp" / "references" / "board.md"
        ).read_text(encoding="utf-8")

        for phrase in (
            "MCP tools",
            "CLI",
            "GitHub Bridge",
            "game_exp_operation_get",
            "game_exp_operation_resume",
            "authorization failure",
            "recovery mode",
            "game_exp_capabilities",
        ):
            self.assertIn(phrase, skill)

        for phrase in (
            "Current public contract: `1.0`",
            "Same id + same request",
            "Same id + different request",
            "Authorization failure",
            "execution.claim",
            "Trusted Writer",
            "Interface availability is not permission",
            "CURSOR_EXPIRED",
            "Handoff schema v2",
            "A2A",
        ):
            self.assertIn(phrase, contract)

        for phrase in (
            "checkpoint_cursor",
            "next_cursor",
            "CURSOR_EXPIRED",
            "event_version",
            "viewer_login",
        ):
            self.assertIn(phrase, notifications)

        for phrase in (
            "Handoff schema v2",
            "build_identity",
            "portable",
            "source SHA",
        ):
            self.assertIn(phrase, handoff)

        for phrase in (
            "DEPENDENCY_REVIEW_REQUIRED",
            "blocks_progress=false",
            "Lifecycle alone is insufficient",
        ):
            self.assertIn(phrase, board)

    def test_plugin_contains_exactly_one_skill_entrypoint(self):
        entrypoints = list(PLUGIN.glob("skills/**/SKILL.md"))
        self.assertEqual(entrypoints, [SKILL])


if __name__ == "__main__":
    unittest.main()
