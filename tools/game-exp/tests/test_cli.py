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

    def test_integrate_routes_to_client(self):
        code, client = self.run_cli(["integrate", "EXP-21"])
        self.assertEqual(code, 0)
        client.integrate.assert_called_once_with("EXP-21")

    def test_integrate_finalize_routes_to_client(self):
        code, client = self.run_cli(
            ["integrate-finalize", "EXP-21", "--pr-number", "35"]
        )
        self.assertEqual(code, 0)
        client.integrate_finalize.assert_called_once_with("EXP-21", "35")

    def test_archive_routes_mode_to_client(self):
        code, client = self.run_cli(
            ["archive", "EXP-21", "--mode", "RETAIN_BRANCH"]
        )
        self.assertEqual(code, 0)
        client.archive.assert_called_once_with("EXP-21", "RETAIN_BRANCH")

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
