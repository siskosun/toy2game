from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))

try:
    import mcp_server  # noqa: E402
except ModuleNotFoundError as exc:
    if exc.name != "mcp":
        raise
    mcp_server = None


class FakeTransport:
    repo = "owner/repo"


class FakeClient:
    transport = FakeTransport()

    def status(self):
        return {"repo": "owner/repo", "ledger_head": "a" * 40}

    def access_check(self):
        return {
            "status": "PASS",
            "repo": "owner/repo",
            "access": {
                "status": "WRITE",
                "can_read": True,
                "can_write": True,
                "can_admin": False,
                "admin_coverage": "PARTIAL",
            },
            "message_zh": "当前具有读写权限，可以使用 game-exp；部分管理员级检查可能不可见。",
            "can_create_experiment": True,
        }

    def capabilities(self):
        return {
            "status": "PASS",
            "repo": "owner/repo",
            "contract": {"version": "1.0"},
            "recovery": {"operation_get": True, "resume_execution": True},
        }

    def board(self, **kwargs):
        return {
            "focus_args": kwargs,
            "status": "PASS",
            "repo": "owner/repo",
            "snapshot_head": "a" * 40,
            "count": 1,
            "counts_by_lifecycle": {"REVIEW": 1},
            "experiments": [
                {
                    "experiment_id": "EXP-21",
                    "lifecycle": "REVIEW",
                    "next_gate": "HUMAN_REVIEW",
                }
            ],
        }

    def subject_panel(self, subject_id):
        return {
            "status": "PASS",
            "repo": "owner/repo",
            "snapshot_head": "a" * 40,
            "subject_id": subject_id,
            "subject": {"id": subject_id, "name": "Arena Duel"},
            "summary": {"count": 1, "attention_count": 1},
            "recent_activity": [],
            "experiments": [{"experiment_id": "EXP-21"}],
            "relationship_edges": [],
        }

    def experiment_panel(self, experiment_id):
        return {
            "status": "PASS",
            "repo": "owner/repo",
            "snapshot_head": "a" * 40,
            "experiment_id": experiment_id,
            "overview": {"lifecycle": "REVIEW", "lifecycle_zh": "评审中"},
            "judgement": {"hypothesis": "test", "success_criteria": [], "kill_criteria": []},
            "activity": [],
            "relationships": {"outgoing": [], "incoming": []},
            "evidence": {"candidate_id": "C-21-123-1"},
        }

    def prototype_handoff(self, experiment_id):
        return {
            "status": "PASS",
            "repo": "owner/repo",
            "experiment_id": experiment_id,
            "handoff_target": "godot-prototype-studio",
            "brief": {"title": "test"},
        }

    def notification_feed(self, **kwargs):
        return {
            "status": "PASS",
            "repo": "owner/repo",
            "viewer_login": kwargs.get("viewer_login"),
            "subject_id": kwargs.get("subject_id"),
            "count": 1,
            "notifications": [{"event_id": "EXP-21:EXPERIMENT_CREATED:EXP-21"}],
        }

    def doctor(self, experiment_id=None):
        return {
            "status": "PASS",
            "repo": "owner/repo",
            "experiment_id": experiment_id,
            "checks": [],
        }

    def reconcile(self, request_id):
        return {"status": "COMMITTED", "request_id": request_id, "repo": "owner/repo"}

    def operation_get(self, request_id):
        return {"status": "COMMITTED", "request_id": request_id, "repo": "owner/repo"}

    def resume_execution(self, request_id):
        return {"status": "ACCEPTED", "request_id": request_id, "operation_status": "DISPATCHED"}

    def experiment_get(self, experiment_id):
        return {
            "status": "PASS",
            "repo": "owner/repo",
            "experiment_id": experiment_id,
            "state": {
                "lifecycle": "REVIEW",
                "current_candidate_id": "C-21-123-1",
                "last_decision_id": "req_previous",
            },
        }

    def initialize(self, experiment_id, *, request_id=None, actor_claim=None):
        return {
            "status": "ACCEPTED",
            "experiment_id": experiment_id,
            "request_id": request_id,
            "actor_claim": actor_claim,
        }

    def candidate(self, experiment_id, *, request_id=None, actor_claim=None):
        return {
            "status": "ACCEPTED",
            "experiment_id": experiment_id,
            "request_id": request_id,
            "actor_claim": actor_claim,
        }

    def rehearse(self, experiment_id, *, request_id=None, actor_claim=None):
        return {
            "status": "ACCEPTED",
            "experiment_id": experiment_id,
            "request_id": request_id,
            "actor_claim": actor_claim,
        }

    def integrate(self, experiment_id, *, request_id=None, actor_claim=None):
        return {
            "status": "ACCEPTED",
            "experiment_id": experiment_id,
            "request_id": request_id,
            "actor_claim": actor_claim,
        }

    def integrate_finalize(
        self, experiment_id, pr_number, *, request_id=None, actor_claim=None
    ):
        return {
            "status": "ACCEPTED",
            "experiment_id": experiment_id,
            "pr_number": str(pr_number),
            "request_id": request_id,
            "actor_claim": actor_claim,
        }

    def archive(
        self, experiment_id, mode, *, request_id=None, actor_claim=None
    ):
        return {
            "status": "ACCEPTED",
            "experiment_id": experiment_id,
            "mode": mode,
            "request_id": request_id,
            "actor_claim": actor_claim,
        }

    def archive_abort(
        self,
        experiment_id,
        archive_id,
        reason,
        *,
        actor_claim=None,
        request_id=None,
    ):
        return {
            "status": "ACCEPTED",
            "experiment_id": experiment_id,
            "archive_id": archive_id,
            "reason": reason,
            "actor_claim": actor_claim,
            "request_id": request_id,
        }

    def submit(self, **kwargs):
        return {
            "status": "ACCEPTED",
            "request_id": kwargs.get("request_id") or "req_generated",
            "repo": "owner/repo",
            "operation": kwargs["operation"],
        }


@unittest.skipIf(mcp_server is None, "official MCP SDK not installed")
class MCPServerTests(unittest.TestCase):
    def test_expected_tools_registered(self):
        tools = asyncio.run(mcp_server.mcp.list_tools())
        names = {tool.name for tool in tools}
        self.assertEqual(
            names,
            {
                "game_exp_conformance_suite",
                "game_exp_conformance_start",
                "game_exp_conformance_compare",
                "game_exp_conformance_result",
                "game_exp_project_preflight",
                "game_exp_project_init",
                "game_exp_status",
                "game_exp_access_check",
                "game_exp_capabilities",
                "game_exp_doctor",
                "game_exp_experiment_get",
                "game_exp_board",
                "game_exp_experiment_panel",
                "game_exp_subject_panel",
                "game_exp_prototype_handoff",
                "game_exp_notifications",
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
                "game_exp_request_get",
                "game_exp_operation_resume",
                "game_exp_request_submit",
            },
        )

    def test_conformance_mcp_uses_same_normal_tool_surface(self):
        with tempfile.TemporaryDirectory() as td:
            session = str(Path(td) / "session.json")
            with patch.dict(
                os.environ,
                {"GAME_EXP_CONFORMANCE_SESSION": session},
                clear=False,
            ):
                started = mcp_server.game_exp_conformance_start(
                    "lost-response-recovery",
                    "mcp-cross-surface",
                )
                self.assertEqual(started["status"], "PASS")

                result = mcp_server.game_exp_operation_get("req-archive-42")
                self.assertEqual(result["status"], "COMMITTED")
                self.assertTrue(result["conformance_simulation"])

                evaluated = mcp_server.game_exp_conformance_result()
                self.assertEqual(evaluated["status"], "PASS")
                data = json.loads(Path(session).read_text(encoding="utf-8"))
                self.assertEqual(data["trace"][0]["surface"], "mcp")
                self.assertEqual(data["trace"][0]["tool"], "game_exp_operation_get")

    def test_conformance_mode_bypasses_real_http_identity_gate_only_for_synthetic_session(self):
        with tempfile.TemporaryDirectory() as td:
            session = str(Path(td) / "session.json")
            with patch.dict(
                os.environ,
                {
                    "GAME_EXP_CONFORMANCE_SESSION": session,
                    "GAME_EXP_MCP_TRANSPORT": "streamable-http",
                },
                clear=False,
            ):
                mcp_server.game_exp_conformance_start(
                    "stale-rehearsal-refresh",
                    "mcp-http-synthetic",
                )
                result = mcp_server.game_exp_rehearse(
                    "EXP-42",
                    "req-rh-http-synthetic",
                )
                self.assertEqual(result["status"], "ACCEPTED")
                self.assertTrue(result["conformance_simulation"])

    def test_conformance_compare_rejects_regression(self):
        baseline = {
            "suite_id": "game-exp-standing-v1",
            "suite_digest": "sha256:" + "a" * 64,
            "eligible_for_real_repo_test": True,
            "passed_count": 6,
            "scenarios": [
                {"scenario_id": "lost-response-recovery", "status": "PASS"},
            ],
        }
        candidate = {
            **baseline,
            "eligible_for_real_repo_test": False,
            "passed_count": 5,
            "scenarios": [
                {"scenario_id": "lost-response-recovery", "status": "FAIL"},
            ],
        }
        result = mcp_server.game_exp_conformance_compare(baseline, candidate)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(len(result["regressions"]), 1)

    def test_tool_schemas_are_explicit(self):
        tools = {tool.name: tool for tool in asyncio.run(mcp_server.mcp.list_tools())}
        submit = tools["game_exp_request_submit"].input_schema
        self.assertIn("operation", submit["properties"])
        self.assertIn("input", submit["properties"])
        self.assertIn("operation", submit["required"])
        self.assertIn("input", submit["required"])
        self.assertIn("request_id", submit["required"])

    def test_tool_annotations_distinguish_reads_from_submit(self):
        tools = {tool.name: tool for tool in asyncio.run(mcp_server.mcp.list_tools())}
        for name in (
            "game_exp_project_preflight",
            "game_exp_status",
            "game_exp_access_check",
            "game_exp_capabilities",
            "game_exp_doctor",
            "game_exp_experiment_get",
            "game_exp_board",
            "game_exp_experiment_panel",
            "game_exp_subject_panel",
            "game_exp_prototype_handoff",
            "game_exp_notifications",
            "game_exp_operation_get",
            "game_exp_request_get",
        ):
            ann = tools[name].annotations
            self.assertTrue(ann.read_only_hint)
            self.assertFalse(ann.destructive_hint)
            self.assertTrue(ann.idempotent_hint)
            self.assertTrue(ann.open_world_hint)

        submit = tools["game_exp_request_submit"].annotations
        self.assertFalse(submit.read_only_hint)
        self.assertFalse(submit.destructive_hint)
        self.assertTrue(submit.idempotent_hint)
        self.assertTrue(submit.open_world_hint)

        archive = tools["game_exp_archive"].annotations
        self.assertFalse(archive.read_only_hint)
        self.assertTrue(archive.destructive_hint)
        self.assertTrue(archive.idempotent_hint)

        resume = tools["game_exp_operation_resume"].annotations
        self.assertFalse(resume.read_only_hint)
        self.assertFalse(resume.destructive_hint)
        self.assertTrue(resume.idempotent_hint)

    def test_project_init_rejects_shared_http(self):
        transport = MagicMock()
        transport.repo = "owner/repo"
        with (
            patch.dict(
                os.environ,
                {"GAME_EXP_MCP_TRANSPORT": "streamable-http"},
                clear=False,
            ),
            patch("mcp_server.GitHubTransport", return_value=transport),
            patch("mcp_server.project_provision") as provision,
        ):
            result = mcp_server.game_exp_project_init("owner/repo")
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["code"], "PROJECT_SETUP_LOCAL_STDIO_REQUIRED")
        provision.assert_not_called()

    def test_project_init_runs_only_under_local_stdio(self):
        transport = MagicMock()
        transport.repo = "owner/repo"
        with (
            patch.dict(
                os.environ,
                {"GAME_EXP_MCP_TRANSPORT": "stdio"},
                clear=False,
            ),
            patch("mcp_server.GitHubTransport", return_value=transport),
            patch(
                "mcp_server.project_provision",
                return_value={"status": "PASS", "complete": True},
            ) as provision,
        ):
            result = mcp_server.game_exp_project_init("owner/repo")
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["complete"])
        provision.assert_called_once_with("owner/repo", run_selftest=True)

    @patch("mcp_server._client", return_value=FakeClient())
    def test_doctor_can_request_archive_health_for_experiment(self, _):
        result = mcp_server.game_exp_doctor("owner/repo", "EXP-21")
        self.assertEqual(result["experiment_id"], "EXP-21")

    @patch("mcp_server._client", return_value=FakeClient())
    def test_read_tools_delegate_to_shared_client(self, _):
        status = mcp_server.game_exp_status("owner/repo")
        access = mcp_server.game_exp_access_check("owner/repo")
        capabilities = mcp_server.game_exp_capabilities("owner/repo")
        doctor = mcp_server.game_exp_doctor("owner/repo")
        request = mcp_server.game_exp_request_get("req_123", "owner/repo")
        operation = mcp_server.game_exp_operation_get("req_123", "owner/repo")
        self.assertEqual(status["ledger_head"], "a" * 40)
        self.assertTrue(access["can_create_experiment"])
        self.assertEqual(access["access"]["status"], "WRITE")
        self.assertEqual(capabilities["contract"]["version"], "1.0")
        self.assertEqual(capabilities["interface"]["transport"], "stdio")
        self.assertEqual(capabilities["interface"]["write_identity"], "local-gh-principal")
        self.assertEqual(doctor["status"], "PASS")
        self.assertEqual(request["status"], "COMMITTED")
        self.assertEqual(operation["status"], "COMMITTED")

    @patch("mcp_server._client", return_value=FakeClient())
    def test_operation_resume_delegates_same_id(self, _):
        result = mcp_server.game_exp_operation_resume(
            "req_exec_21",
            repo="owner/repo",
        )
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertEqual(result["request_id"], "req_exec_21")

    @patch("mcp_server._client", return_value=FakeClient())
    def test_experiment_projection_delegates_to_client(self, _):
        result = mcp_server.game_exp_experiment_get("EXP-21", "owner/repo")
        self.assertEqual(result["state"]["lifecycle"], "REVIEW")

    @patch("mcp_server._client", return_value=FakeClient())
    def test_board_projection_delegates_to_client(self, _):
        result = mcp_server.game_exp_board("owner/repo")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["experiments"][0]["next_gate"], "HUMAN_REVIEW")

    @patch("mcp_server._client", return_value=FakeClient())
    def test_experiment_panel_delegates_to_client(self, _):
        result = mcp_server.game_exp_experiment_panel("EXP-21", "owner/repo")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["experiment_id"], "EXP-21")
        self.assertEqual(result["overview"]["lifecycle_zh"], "评审中")

    @patch("mcp_server._client", return_value=FakeClient())
    def test_subject_panel_delegates_to_client(self, _):
        result = mcp_server.game_exp_subject_panel("arena-duel", "owner/repo")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["subject_id"], "arena-duel")
        self.assertEqual(result["subject"]["name"], "Arena Duel")

    @patch("mcp_server._client", return_value=FakeClient())
    def test_prototype_handoff_delegates_to_client(self, _):
        result = mcp_server.game_exp_prototype_handoff("EXP-21", "owner/repo")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["handoff_target"], "godot-prototype-studio")

    @patch("mcp_server._client", return_value=FakeClient())
    def test_notifications_delegate_to_client(self, _):
        result = mcp_server.game_exp_notifications(
            repo="owner/repo",
            viewer_login="bob",
            subject_id="arena-duel",
            limit=20,
            after="n1.old",
            cursor=None,
        )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["viewer_login"], "bob")
        self.assertEqual(result["subject_id"], "arena-duel")
        self.assertEqual(result["count"], 1)

    @patch("mcp_server._client", return_value=FakeClient())
    def test_board_focus_filters_delegate_to_client(self, _):
        result = mcp_server.game_exp_board(
            repo="owner/repo",
            query="combat",
            subject_id="arena-duel",
            lifecycle="review",
            attention_only=True,
        )
        self.assertEqual(
            result["focus_args"],
            {
                "query": "combat",
                "subject_id": "arena-duel",
                "lifecycle": "review",
                "attention_only": True,
            },
        )

    @patch("mcp_server._client", return_value=FakeClient())
    def test_review_defaults_to_current_candidate(self, _):
        result = mcp_server.game_exp_review_record(
            experiment_id="EXP-21",
            outcome="PASS",
            notes="human playtest",
            request_id="req_review_21",
            repo="owner/repo",
        )
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertEqual(result["operation"], "review.record")

    @patch("mcp_server._client", return_value=FakeClient())
    def test_decision_defaults_to_current_previous_decision(self, _):
        result = mcp_server.game_exp_decision_submit(
            experiment_id="EXP-21",
            to_state="PROMISING",
            reason="passed current review",
            request_id="req_decision_21",
            repo="owner/repo",
        )
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertEqual(result["operation"], "experiment.decision")

    @patch("mcp_server._client", return_value=FakeClient())
    def test_workflow_domain_tools_delegate_to_shared_client(self, _):
        self.assertEqual(
            mcp_server.game_exp_initialize(
                "EXP-21", "req_init_21", repo="owner/repo"
            )["status"],
            "ACCEPTED",
        )
        self.assertEqual(
            mcp_server.game_exp_candidate_build(
                "EXP-21", "req_candidate_21", repo="owner/repo"
            )["status"],
            "ACCEPTED",
        )
        self.assertEqual(
            mcp_server.game_exp_rehearse(
                "EXP-21", "req_rehearse_21", repo="owner/repo"
            )["status"],
            "ACCEPTED",
        )
        self.assertEqual(
            mcp_server.game_exp_integrate(
                "EXP-21", "req_integrate_21", repo="owner/repo"
            )["status"],
            "ACCEPTED",
        )
        self.assertEqual(
            mcp_server.game_exp_integrate_finalize(
                "EXP-21", "35", "req_finalize_21", repo="owner/repo"
            )["status"],
            "ACCEPTED",
        )
        archive = mcp_server.game_exp_archive(
            "EXP-21",
            "req_archive_21",
            mode="ATOMIC_DELETE",
            repo="owner/repo",
        )
        self.assertEqual(archive["status"], "ACCEPTED")

    @patch("mcp_server._client", return_value=FakeClient())
    def test_bind_and_archive_abort_remain_controlled_client_actions(self, _):
        bind = mcp_server.game_exp_experiment_bind(
            {"schema_version": 1, "operation_id": "req_bind"},
            request_id="req_bind",
            repo="owner/repo",
        )
        self.assertEqual(bind["operation"], "experiment.bind")
        abort = mcp_server.game_exp_archive_abort(
            "EXP-21",
            "A-21-1",
            "cancel before claim",
            request_id="req_abort",
            repo="owner/repo",
        )
        self.assertEqual(abort["archive_id"], "A-21-1")

    @patch("mcp_server._client", return_value=FakeClient())
    def test_bind_rejects_request_id_different_from_manifest_operation_id(self, _):
        result = mcp_server.game_exp_experiment_bind(
            {"schema_version": 1, "operation_id": "req_manifest"},
            request_id="req_other",
            repo="owner/repo",
        )
        self.assertEqual(result["status"], "REJECTED")
        self.assertIn("must equal", result["error"])

    @patch("mcp_server._client", return_value=FakeClient())
    def test_submit_is_request_transport_not_domain_execution(self, _):
        result = mcp_server.game_exp_request_submit(
            operation="experiment.create",
            input={"hypothesis": "three roles"},
            preconditions={"state": "ACTIVE"},
            actor_claim="human-reviewer",
            request_id="req_123",
            repo="owner/repo",
        )
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertEqual(result["operation"], "experiment.create")

    @patch("mcp_server._client", return_value=FakeClient())
    def test_streamable_http_writes_fail_closed_without_bound_identity(self, _):
        env = {"GAME_EXP_MCP_TRANSPORT": "streamable-http"}
        with patch.dict(mcp_server.os.environ, env, clear=True):
            result = mcp_server.game_exp_candidate_build(
                "EXP-21",
                "req_candidate_http",
                repo="owner/repo",
            )
            capabilities = mcp_server.game_exp_capabilities("owner/repo")
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["code"], "MCP_HTTP_WRITE_IDENTITY_UNBOUND")
        self.assertEqual(
            capabilities["interface"]["write_identity"],
            "unbound-read-only",
        )

    @patch("mcp_server._client", return_value=FakeClient())
    def test_streamable_http_single_principal_can_use_trusted_write_path(self, _):
        env = {
            "GAME_EXP_MCP_TRANSPORT": "streamable-http",
            "GAME_EXP_MCP_TRUSTED_SINGLE_PRINCIPAL": "1",
        }
        with patch.dict(mcp_server.os.environ, env, clear=True):
            result = mcp_server.game_exp_candidate_build(
                "EXP-21",
                "req_candidate_http",
                repo="owner/repo",
            )
        self.assertEqual(result["status"], "ACCEPTED")

    def test_server_run_defaults_to_stdio(self):
        with patch.dict(mcp_server.os.environ, {}, clear=True):
            with patch.object(mcp_server.mcp, "run") as run:
                mcp_server._run_server()
        run.assert_called_once_with()

    def test_server_run_supports_streamable_http_for_chatgpt(self):
        env = {
            "GAME_EXP_MCP_TRANSPORT": "streamable-http",
            "GAME_EXP_MCP_HOST": "127.0.0.1",
            "GAME_EXP_MCP_PORT": "9876",
            "GAME_EXP_MCP_PATH": "/game-exp-mcp",
        }
        with patch.dict(mcp_server.os.environ, env, clear=True):
            with patch.object(mcp_server.mcp, "run") as run:
                mcp_server._run_server()
        run.assert_called_once_with(
            transport="streamable-http",
            host="127.0.0.1",
            port=9876,
            streamable_http_path="/game-exp-mcp",
            stateless_http=True,
            json_response=True,
        )

    def test_server_run_rejects_invalid_http_port(self):
        env = {
            "GAME_EXP_MCP_TRANSPORT": "streamable-http",
            "GAME_EXP_MCP_PORT": "not-a-port",
        }
        with patch.dict(mcp_server.os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "must be an integer"):
                mcp_server._run_server()


if __name__ == "__main__":
    unittest.main()
