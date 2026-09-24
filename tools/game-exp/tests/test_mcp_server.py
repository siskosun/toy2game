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


class FakeClient:
    def status(self):
        return {"repo": "owner/repo", "ledger_head": "a" * 40}

    def doctor(self, experiment_id=None):
        return {
            "status": "PASS",
            "repo": "owner/repo",
            "experiment_id": experiment_id,
            "checks": [],
        }

    def reconcile(self, request_id):
        return {"status": "COMMITTED", "request_id": request_id, "repo": "owner/repo"}

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
        for name in ("game_exp_status", "game_exp_doctor", "game_exp_request_get"):
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


if __name__ == "__main__":
    unittest.main()
