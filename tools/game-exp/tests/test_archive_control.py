from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))

import archive_control  # noqa: E402


class ArchiveControlTests(unittest.TestCase):
    def test_prepare_payload_binds_workflow_run(self):
        out = archive_control.request_payload(
            SimpleNamespace(
                operation="prepare",
                experiment_id="EXP-21",
                archive_id=None,
                mode="ATOMIC_DELETE",
                run_id="123",
                run_attempt="2",
                actor_claim="github-workflow-dispatch:reviewer",
            )
        )
        self.assertEqual(out["request_id"], "req-archive-prepare-21-123-2")
        self.assertEqual(out["payload"]["operation"], "archive.prepare")
        self.assertEqual(out["payload"]["input"]["mode"], "ATOMIC_DELETE")

    def test_claim_payload_is_stable_for_archive_identity(self):
        out = archive_control.request_payload(
            SimpleNamespace(
                operation="claim",
                experiment_id="EXP-21",
                archive_id="A-21-1",
                mode="ATOMIC_DELETE",
                run_id="123",
                run_attempt="1",
                actor_claim=None,
            )
        )
        self.assertEqual(out["request_id"], "req-archive-claim-A-21-1")

    @patch("archive_control.github_json")
    @patch("archive_control.github_content_json")
    def test_atomic_remote_state_not_executed(self, content, api):
        content.return_value = {
            "phase": "CLAIMED",
            "mode": "ATOMIC_DELETE",
            "expected_branch_sha": "b" * 40,
            "branch_ref": "refs/heads/exp/21",
            "final_tag_ref": "refs/tags/exp-final/21",
        }
        api.side_effect = [
            {"object": {"type": "commit", "sha": "b" * 40}},
            None,
        ]
        out = archive_control.remote_state(
            SimpleNamespace(
                repo="owner/repo",
                experiment_id="EXP-21",
                archive_id="A-21-1",
            )
        )
        self.assertEqual(out["classification"], "NOT_EXECUTED")

    @patch("archive_control.github_json")
    @patch("archive_control.github_content_json")
    def test_atomic_remote_state_committed(self, content, api):
        content.return_value = {
            "phase": "CLAIMED",
            "mode": "ATOMIC_DELETE",
            "expected_branch_sha": "b" * 40,
            "branch_ref": "refs/heads/exp/21",
            "final_tag_ref": "refs/tags/exp-final/21",
        }
        api.side_effect = [
            None,
            {"object": {"type": "tag", "sha": "1" * 40}},
            {"object": {"type": "commit", "sha": "b" * 40}},
        ]
        out = archive_control.remote_state(
            SimpleNamespace(
                repo="owner/repo",
                experiment_id="EXP-21",
                archive_id="A-21-1",
            )
        )
        self.assertEqual(out["classification"], "REF_COMMITTED")
        self.assertIsNone(out["branch_sha"])

    @patch("archive_control.github_json")
    @patch("archive_control.github_content_json")
    def test_retain_branch_drift_is_conflict(self, content, api):
        content.return_value = {
            "phase": "CLAIMED",
            "mode": "RETAIN_BRANCH",
            "expected_branch_sha": "b" * 40,
            "branch_ref": "refs/heads/exp/21",
            "final_tag_ref": "refs/tags/exp-final/21",
        }
        api.side_effect = [
            {"object": {"type": "commit", "sha": "c" * 40}},
            {"object": {"type": "tag", "sha": "1" * 40}},
            {"object": {"type": "commit", "sha": "b" * 40}},
        ]
        out = archive_control.remote_state(
            SimpleNamespace(
                repo="owner/repo",
                experiment_id="EXP-21",
                archive_id="A-21-1",
            )
        )
        self.assertEqual(out["classification"], "REF_CONFLICT")


if __name__ == "__main__":
    unittest.main()
