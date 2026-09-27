from __future__ import annotations

import json
import sys
import tempfile
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

    def test_project_preflight_routes_before_client_construction(self):
        transport = MagicMock()
        transport.repo = "owner/repo"
        with (
            patch("cli.GitHubTransport", return_value=transport),
            patch(
                "cli.project_preflight",
                return_value={"status": "PASS", "ready_to_provision": True},
            ) as preflight,
            patch("cli._print_result"),
        ):
            code = cli.main(["--repo", "owner/repo", "project-preflight"])
        self.assertEqual(code, 0)
        preflight.assert_called_once_with("owner/repo")

    def test_project_init_requires_complete_pass(self):
        transport = MagicMock()
        transport.repo = "owner/repo"
        with (
            patch("cli.GitHubTransport", return_value=transport),
            patch(
                "cli.project_provision",
                return_value={"status": "PASS", "complete": True},
            ) as provision,
            patch("cli._print_result"),
        ):
            code = cli.main(["--repo", "owner/repo", "project-init"])
        self.assertEqual(code, 0)
        provision.assert_called_once_with("owner/repo", run_selftest=True)

    def test_project_init_skip_selftest_is_incomplete(self):
        transport = MagicMock()
        transport.repo = "owner/repo"
        with (
            patch("cli.GitHubTransport", return_value=transport),
            patch(
                "cli.project_provision",
                return_value={"status": "PASS", "complete": True},
            ),
            patch("cli._print_result") as printer,
        ):
            code = cli.main(
                ["--repo", "owner/repo", "project-init", "--skip-selftest"]
            )
        self.assertEqual(code, 1)
        result = printer.call_args.args[0]
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertFalse(result["complete"])

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
        self.assertEqual(
            client.capabilities.return_value["interface"]["write_identity"],
            "local-gh-principal",
        )

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

    def test_conformance_session_uses_normal_cli_surface_without_github(self):
        with tempfile.TemporaryDirectory() as td:
            session = Path(td) / "session.json"
            with patch("cli._print_result"):
                code = cli.main(
                    [
                        "--json",
                        "conformance-start",
                        "lost-response-recovery",
                        "--session-file",
                        str(session),
                        "--session-id",
                        "cli-cross-surface",
                    ]
                )
            self.assertEqual(code, 0)
            self.assertTrue(session.exists())

            with (
                patch("cli.GitHubTransport") as github_transport,
                patch("cli._print_result"),
            ):
                code = cli.main(
                    [
                        "--conformance-session",
                        str(session),
                        "--json",
                        "get-operation",
                        "req-archive-42",
                    ]
                )
            self.assertEqual(code, 0)
            github_transport.assert_not_called()

            data = json.loads(session.read_text(encoding="utf-8"))
            self.assertEqual(data["trace"][-1]["surface"], "cli")
            self.assertEqual(data["trace"][-1]["tool"], "game_exp_operation_get")

    def test_conformance_result_and_report_are_local_only(self):
        with tempfile.TemporaryDirectory() as td:
            files = []
            scenarios = [
                ("lost-response-recovery", ["get-operation", "req-archive-42"], 0),
                (
                    "authorization-no-fallback",
                    ["get-operation", "req-promote-42"],
                    1,
                ),
                ("review-bound-to-candidate", ["experiment", "EXP-42"], 0),
                ("dependency-review-required", ["experiment", "EXP-86"], 0),
                ("human-gate-preserved", ["experiment", "EXP-42"], 0),
            ]
            for scenario_id, command, expected_code in scenarios:
                path = Path(td) / f"{scenario_id}.json"
                files.append(path)
                with patch("cli._print_result"):
                    self.assertEqual(
                        cli.main(
                            [
                                "conformance-start",
                                scenario_id,
                                "--session-file",
                                str(path),
                            ]
                        ),
                        0,
                    )
                    self.assertEqual(
                        cli.main(
                            [
                                "--conformance-session",
                                str(path),
                                *command,
                            ]
                        ),
                        expected_code,
                    )

            stale = Path(td) / "stale.json"
            files.append(stale)
            with patch("cli._print_result"):
                self.assertEqual(
                    cli.main(
                        [
                            "conformance-start",
                            "stale-rehearsal-refresh",
                            "--session-file",
                            str(stale),
                        ]
                    ),
                    0,
                )
                self.assertEqual(
                    cli.main(
                        [
                            "--conformance-session",
                            str(stale),
                            "rehearse",
                            "EXP-42",
                            "--request-id",
                            "req-rh-cli",
                        ]
                    ),
                    0,
                )
                self.assertEqual(
                    cli.main(
                        [
                            "--conformance-session",
                            str(stale),
                            "get-operation",
                            "req-rh-cli",
                        ]
                    ),
                    0,
                )

                argv = ["conformance-report"]
                for path in files:
                    argv.extend(["--session-file", str(path)])
                self.assertEqual(cli.main(argv), 0)

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
