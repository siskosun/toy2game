from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def board(self):
        return {
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

    def doctor(self, experiment_id=None):
        return {
            "status": "PASS",
            "repo": "owner/repo",
            "experiment_id": experiment_id,
            "checks": [],
        }

    def reconcile(self, request_id):
        return {"status": "COMMITTED", "request_id": request_id, "repo": "owner/repo"}

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

    def initialize(self, experiment_id):
        return {"status": "ACCEPTED", "experiment_id": experiment_id}

    def candidate(self, experiment_id):
        return {"status": "ACCEPTED", "experiment_id": experiment_id}

    def rehearse(self, experiment_id):
        return {"status": "ACCEPTED", "experiment_id": experiment_id}

    def integrate(self, experiment_id):
        return {"status": "ACCEPTED", "experiment_id": experiment_id}

    def integrate_finalize(self, experiment_id, pr_number):
        return {
            "status": "ACCEPTED",
            "experiment_id": experiment_id,
            "pr_number": str(pr_number),
        }

    def archive(self, experiment_id, mode):
        return {
            "status": "ACCEPTED",
            "experiment_id": experiment_id,
            "mode": mode,
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
                "game_exp_status",
                "game_exp_doctor",
                "game_exp_experiment_get",
                "game_exp_board",
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
                "game_exp_request_submit",
            },
        )

    def test_tool_schemas_are_explicit(self):
        tools = {tool.name: tool for tool in asyncio.run(mcp_server.mcp.list_tools())}
        submit = tools["game_exp_request_submit"].input_schema
        self.assertIn("operation", submit["properties"])
        self.assertIn("input", submit["properties"])
        self.assertIn("operation", submit["required"])
        self.assertIn("input", submit["required"])

    def test_tool_annotations_distinguish_reads_from_submit(self):
        tools = {tool.name: tool for tool in asyncio.run(mcp_server.mcp.list_tools())}
        for name in (
            "game_exp_status",
            "game_exp_doctor",
            "game_exp_experiment_get",
            "game_exp_board",
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
        self.assertFalse(submit.idempotent_hint)
        self.assertTrue(submit.open_world_hint)

        archive = tools["game_exp_archive"].annotations
        self.assertFalse(archive.read_only_hint)
        self.assertTrue(archive.destructive_hint)
        self.assertTrue(archive.idempotent_hint)

    @patch("mcp_server._client", return_value=FakeClient())
    def test_doctor_can_request_archive_health_for_experiment(self, _):
        result = mcp_server.game_exp_doctor("owner/repo", "EXP-21")
        self.assertEqual(result["experiment_id"], "EXP-21")

    @patch("mcp_server._client", return_value=FakeClient())
    def test_read_tools_delegate_to_shared_client(self, _):
        status = mcp_server.game_exp_status("owner/repo")
        doctor = mcp_server.game_exp_doctor("owner/repo")
        request = mcp_server.game_exp_request_get("req_123", "owner/repo")
        self.assertEqual(status["ledger_head"], "a" * 40)
        self.assertEqual(doctor["status"], "PASS")
        self.assertEqual(request["status"], "COMMITTED")

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
    def test_review_defaults_to_current_candidate(self, _):
        result = mcp_server.game_exp_review_record(
            experiment_id="EXP-21",
            outcome="PASS",
            notes="human playtest",
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
            repo="owner/repo",
        )
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertEqual(result["operation"], "experiment.decision")

    @patch("mcp_server._client", return_value=FakeClient())
    def test_workflow_domain_tools_delegate_to_shared_client(self, _):
        self.assertEqual(
            mcp_server.game_exp_initialize("EXP-21", "owner/repo")["status"],
            "ACCEPTED",
        )
        self.assertEqual(
            mcp_server.game_exp_candidate_build("EXP-21", "owner/repo")["status"],
            "ACCEPTED",
        )
        self.assertEqual(
            mcp_server.game_exp_rehearse("EXP-21", "owner/repo")["status"],
            "ACCEPTED",
        )
        self.assertEqual(
            mcp_server.game_exp_integrate("EXP-21", "owner/repo")["status"],
            "ACCEPTED",
        )
        self.assertEqual(
            mcp_server.game_exp_integrate_finalize("EXP-21", "35", "owner/repo")["status"],
            "ACCEPTED",
        )
        archive = mcp_server.game_exp_archive("EXP-21", "ATOMIC_DELETE", "owner/repo")
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
