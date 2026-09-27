from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))

import archive_runner  # noqa: E402
import cli  # noqa: E402
import client  # noqa: E402
import github_bridge  # noqa: E402
import source_initializer  # noqa: E402
import trusted_writer  # noqa: E402


class _Stream:
    def __init__(self):
        self.calls = []

    def reconfigure(self, **kwargs):
        self.calls.append(kwargs)


class WindowsUtf8ContractTests(unittest.TestCase):
    def _assert_strict_utf8(self, run_mock):
        kwargs = run_mock.call_args.kwargs
        self.assertTrue(kwargs["text"])
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(kwargs["errors"], "strict")

    def test_client_subprocess_uses_strict_utf8(self):
        completed = subprocess.CompletedProcess(["gh"], 0, "中文\n", "")
        with patch("client.subprocess.run", return_value=completed) as run_mock:
            result = client._run(["gh", "--version"])
        self.assertEqual(result.stdout, "中文\n")
        self._assert_strict_utf8(run_mock)

    def test_archive_runner_subprocess_uses_strict_utf8(self):
        completed = subprocess.CompletedProcess(["git"], 0, "中文\n", "")
        with patch("archive_runner.subprocess.run", return_value=completed) as run_mock:
            result = archive_runner.run(["git", "status"])
        self.assertEqual(result.stdout, "中文\n")
        self._assert_strict_utf8(run_mock)

    def test_source_initializer_subprocess_uses_strict_utf8(self):
        completed = subprocess.CompletedProcess(["git"], 0, "中文\n", "")
        with patch(
            "source_initializer.subprocess.run", return_value=completed
        ) as run_mock:
            result = source_initializer.run(["git", "status"])
        self.assertEqual(result.stdout, "中文\n")
        self._assert_strict_utf8(run_mock)

    def test_trusted_writer_subprocess_uses_strict_utf8(self):
        completed = subprocess.CompletedProcess(["git"], 0, "中文\n", "")
        with patch("trusted_writer.subprocess.run", return_value=completed) as run_mock:
            result = trusted_writer.run(["git", "status"])
        self.assertEqual(result.stdout, "中文\n")
        self._assert_strict_utf8(run_mock)

    def test_github_bridge_dispatch_uses_strict_utf8(self):
        completed = subprocess.CompletedProcess(
            ["gh"],
            0,
            "https://github.com/owner/repo/actions/runs/123\n",
            "",
        )
        with (
            patch.dict(os.environ, {"GAME_EXP_GITHUB_TOKEN": "token"}, clear=False),
            patch("github_bridge.subprocess.run", return_value=completed) as run_mock,
        ):
            result = github_bridge._dispatch_workflow(
                "owner/repo",
                "game-exp-candidate.yml",
                {"experiment_id": "EXP-1"},
                request_id="req_utf8_1",
            )
        self.assertEqual(result["status"], "ACCEPTED")
        self._assert_strict_utf8(run_mock)

    def test_windows_cli_reconfigures_display_streams_as_utf8(self):
        stdout = _Stream()
        stderr = _Stream()
        with (
            patch.object(cli.sys, "platform", "win32"),
            patch.object(cli.sys, "stdout", stdout),
            patch.object(cli.sys, "stderr", stderr),
        ):
            cli._configure_windows_stdio_utf8()
        self.assertEqual(
            stdout.calls,
            [{"encoding": "utf-8", "errors": "replace"}],
        )
        self.assertEqual(
            stderr.calls,
            [{"encoding": "utf-8", "errors": "replace"}],
        )

    def test_non_windows_cli_leaves_streams_untouched(self):
        stdout = _Stream()
        stderr = _Stream()
        with (
            patch.object(cli.sys, "platform", "linux"),
            patch.object(cli.sys, "stdout", stdout),
            patch.object(cli.sys, "stderr", stderr),
        ):
            cli._configure_windows_stdio_utf8()
        self.assertEqual(stdout.calls, [])
        self.assertEqual(stderr.calls, [])


if __name__ == "__main__":
    unittest.main()
