from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))

import cli  # noqa: E402


class CLIRoutingTests(unittest.TestCase):
    def run_cli(self, argv):
        transport = MagicMock()
        client = MagicMock()
        client.status.return_value = {"status": "PASS"}
        client.capabilities.return_value = {"status": "PASS", "contract": {"version": "1.0"}}
        client.board.return_value = {"status": "PASS", "experiments": []}
        client.experiment_get.return_value = {"status": "PASS", "state": {"current_candidate_id": "C-21-1-1", "last_decision_id": "req_prev"}}
        client.notification_feed.return_value = {"status": "PASS", "notifications": []}
        client.prototype_handoff.return_value = {"status": "PASS"}
        client.operation_get.return_value = {"status": "COMMITTED"}
        client.resume_execution.return_value = {"status": "ACCEPTED"}
        client.integrate.return_value = {"status": "ACCEPTED"}
        client.integrate_finalize.return_value = {"status": "ACCEPTED"}
        client.archive.return_value = {"status": "ACCEPTED"}
        client.archive_abort.return_value = {"status": "ACCEPTED"}
        with (
            patch("cli.GitHubTransport", return_value=transport),
            patch("cli.GameExpClient", return_value=client),
            patch("cli._print_result"),
        ):
            code = cli.main(argv)
        return code, client

    def test_board_routes_to_client(self):
        code, client = self.run_cli(["board"])
        self.assertEqual(code, 0)
        client.board.assert_called_once_with(
            query=None,
            subject_id=None,
            lifecycle=None,
            attention_only=False,
        )

    def test_integrate_routes_to_client(self):
        code, client = self.run_cli(["integrate", "EXP-21", "--request-id", "req_integrate_21"])
        self.assertEqual(code, 0)
        client.integrate.assert_called_once_with(
            "EXP-21", request_id="req_integrate_21", actor_claim=None
        )

    def test_integrate_finalize_routes_to_client(self):
        code, client = self.run_cli(
            ["integrate-finalize", "EXP-21", "--pr-number", "35", "--request-id", "req_finalize_21"]
        )
        self.assertEqual(code, 0)
        client.integrate_finalize.assert_called_once_with(
            "EXP-21", "35", request_id="req_finalize_21", actor_claim=None
        )

    def test_archive_routes_mode_to_client(self):
        code, client = self.run_cli(
            [
                "archive",
                "EXP-21",
                "--mode",
                "RETAIN_BRANCH",
                "--request-id",
                "req_archive_21",
            ]
        )
        self.assertEqual(code, 0)
        client.archive.assert_called_once_with(
            "EXP-21",
            "RETAIN_BRANCH",
            request_id="req_archive_21",
            actor_claim=None,
        )

    def test_capabilities_routes_to_client(self):
        code, client = self.run_cli(["capabilities"])
        self.assertEqual(code, 0)
        client.capabilities.assert_called_once_with()

    def test_get_and_resume_operation_route_without_resubmit(self):
        code, client = self.run_cli(["get-operation", "req_shared_1"])
        self.assertEqual(code, 0)
        client.operation_get.assert_called_once_with("req_shared_1")

        code, client = self.run_cli(["resume-operation", "req_shared_1"])
        self.assertEqual(code, 0)
        client.resume_execution.assert_called_once_with("req_shared_1")

    def test_notifications_passes_resume_cursors(self):
        code, client = self.run_cli(
            [
                "notifications",
                "--viewer",
                "bob",
                "--subject-id",
                "arena-duel",
                "--after",
                "n1.checkpoint",
            ]
        )
        self.assertEqual(code, 0)
        client.notification_feed.assert_called_once_with(
            viewer_login="bob",
            subject_id="arena-duel",
            limit=50,
            after="n1.checkpoint",
            cursor=None,
        )

    def test_mutating_async_commands_require_request_id(self):
        with self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["integrate", "EXP-21"])

    def test_archive_abort_routes_human_request(self):
        code, client = self.run_cli(
            [
                "archive-abort",
                "EXP-21",
                "--archive-id",
                "A-21-1",
                "--reason",
                "cancel before claim",
                "--request-id",
                "req_abort_1",
                "--actor-claim",
                "reviewer",
            ]
        )
        self.assertEqual(code, 0)
        client.archive_abort.assert_called_once_with(
            "EXP-21",
            "A-21-1",
            "cancel before claim",
            actor_claim="reviewer",
            request_id="req_abort_1",
        )


if __name__ == "__main__":
    unittest.main()
