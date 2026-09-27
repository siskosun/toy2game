from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))

import github_bridge  # noqa: E402


class GitHubBridgeTests(unittest.TestCase):
    def command(self, payload):
        return "/game-exp\n" + json.dumps(payload, ensure_ascii=False)

    def test_parse_status_command(self):
        result = github_bridge.parse_command(
            self.command(
                {
                    "schema_version": 1,
                    "request_id": "req_bridge_status_50",
                    "action": "status",
                    "experiment_id": "EXP-50",
                }
            )
        )
        self.assertEqual(result["action"], "status")

    def test_parse_rejects_extra_keys(self):
        with self.assertRaisesRegex(github_bridge.BridgeError, "keys mismatch"):
            github_bridge.parse_command(
                self.command(
                    {
                        "schema_version": 1,
                        "request_id": "req_bridge_bad",
                        "action": "rehearse",
                        "experiment_id": "EXP-50",
                        "unexpected": True,
                    }
                )
            )

    def test_bind_request_must_match_manifest_operation_id(self):
        command = {
            "schema_version": 1,
            "request_id": "req_bridge_bind_50",
            "action": "bind",
            "manifest": {
                "operation_id": "req_other",
                "experiment": {"issue_number": "50"},
            },
        }
        with self.assertRaisesRegex(github_bridge.BridgeError, "operation_id"):
            github_bridge.validate_issue_binding(command, "50")

    def test_experiment_must_match_comment_issue(self):
        command = {
            "schema_version": 1,
            "request_id": "req_bridge_rehearse_50",
            "action": "rehearse",
            "experiment_id": "EXP-50",
        }
        with self.assertRaisesRegex(github_bridge.BridgeError, "match"):
            github_bridge.validate_issue_binding(command, "51")

    @patch("github_bridge.github_json")
    def test_verify_actor_requires_write_permission(self, github_json):
        github_json.return_value = {
            "permission": "read",
            "user": {"login": "alice", "id": 1},
        }
        with self.assertRaisesRegex(github_bridge.BridgeError, "lacks write"):
            github_bridge.verify_actor("owner/repo", "alice")

    @patch("github_bridge.github_json")
    def test_verify_actor_accepts_admin(self, github_json):
        github_json.return_value = {
            "permission": "admin",
            "user": {"login": "alice", "id": 1},
        }
        self.assertEqual(
            github_bridge.verify_actor("owner/repo", "alice"),
            "admin",
        )

    @patch("github_bridge._claim_async_execution")
    @patch("github_bridge._dispatch_workflow")
    def test_rehearse_routes_to_existing_trusted_workflow(self, dispatch, claim):
        claim.return_value = {"status": "COMMITTED"}
        dispatch.return_value = {"status": "ACCEPTED"}
        result = github_bridge.execute_action(
            {
                "schema_version": 1,
                "request_id": "req_bridge_rehearse_50",
                "action": "rehearse",
                "experiment_id": "EXP-50",
            },
            repo="owner/repo",
            actor_login="alice",
            comment_id="123",
            ssh_key="/tmp/key",
            run_id="456",
            run_attempt="1",
            workflow_source_sha="a" * 40,
        )
        self.assertEqual(result["status"], "ACCEPTED")
        claim.assert_called_once()
        self.assertEqual(claim.call_args.args[0]["request_id"], "req_bridge_rehearse_50")
        dispatch.assert_called_once_with(
            "owner/repo",
            "game-exp-rehearsal.yml",
            {"experiment_id": "EXP-50"},
            request_id="req_bridge_rehearse_50",
        )

    @patch("github_bridge._claim_async_execution")
    @patch("github_bridge._dispatch_workflow")
    def test_archive_uses_same_claim_and_worker_contract(self, dispatch, claim):
        claim.return_value = {"status": "COMMITTED"}
        dispatch.return_value = {"status": "ACCEPTED"}
        command = {
            "schema_version": 1,
            "request_id": "req_bridge_archive_50",
            "action": "archive",
            "experiment_id": "EXP-50",
            "mode": "ATOMIC_DELETE",
        }
        result = github_bridge.execute_action(
            command,
            repo="owner/repo",
            actor_login="alice",
            comment_id="123",
            ssh_key="/tmp/key",
            run_id="456",
            run_attempt="1",
            workflow_source_sha="a" * 40,
        )
        self.assertEqual(result["status"], "ACCEPTED")
        claim.assert_called_once()
        dispatch.assert_called_once_with(
            "owner/repo",
            "game-exp-archive.yml",
            {"experiment_id": "EXP-50", "mode": "ATOMIC_DELETE"},
            request_id="req_bridge_archive_50",
        )

    @patch("github_bridge._token", return_value="token")
    @patch("github_bridge.subprocess.run")
    def test_worker_dispatch_failure_is_unknown_not_rejected(self, run, _token):
        run.return_value.returncode = 1
        run.return_value.stdout = ""
        run.return_value.stderr = "network uncertain"
        with self.assertRaises(github_bridge.BridgeUncertainError):
            github_bridge._dispatch_workflow(
                "owner/repo",
                "game-exp-candidate.yml",
                {"experiment_id": "EXP-50"},
                request_id="req_bridge_candidate_50",
            )

    def test_marker_is_request_scoped(self):
        self.assertEqual(
            github_bridge._marker("req_bridge_50", "claim"),
            "<!-- game-exp-bridge:req_bridge_50:claim -->",
        )


if __name__ == "__main__":
    unittest.main()
