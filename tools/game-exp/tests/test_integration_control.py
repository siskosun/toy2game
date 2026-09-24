from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))

from integration_control import (  # noqa: E402
    IntegrationControlError,
    finalize,
    prepare,
    request_payload,
)
from protocol_core import decode_payload_b64  # noqa: E402


class IntegrationControlTests(unittest.TestCase):
    @patch("integration_control.github_json")
    @patch("integration_control.github_content_json")
    def test_prepare_requires_selected_current_rehearsal_on_current_main(self, content, api):
        content.side_effect = [
            {
                "experiment_id": "EXP-21",
                "lifecycle": "SELECTED",
                "current_candidate_id": "C-21-123-1",
                "current_rehearsal_id": "R-21-456-1",
            },
            {
                "kind": "rehearsal",
                "candidate_id": "C-21-123-1",
                "rehearsal_id": "R-21-456-1",
                "main_sha": "a" * 40,
                "source_sha": "b" * 40,
                "integration_tree_sha": "c" * 40,
                "rehearsal_ref": "refs/tags/exp-rehearsal/21/R-21-456-1",
            },
        ]
        api.return_value = {"object": {"sha": "a" * 40}}
        out = prepare(
            argparse.Namespace(repo="owner/repo", experiment_id="EXP-21")
        )
        self.assertEqual(out["branch"], "game-exp/integration/21/R-21-456-1")
        self.assertEqual(out["integration_tree_sha"], "c" * 40)
        self.assertEqual(out["main_sha"], "a" * 40)

    @patch("integration_control.github_json")
    @patch("integration_control.github_content_json")
    def test_prepare_rejects_stale_rehearsal_main(self, content, api):
        content.side_effect = [
            {
                "experiment_id": "EXP-21",
                "lifecycle": "SELECTED",
                "current_candidate_id": "C-21-123-1",
                "current_rehearsal_id": "R-21-456-1",
            },
            {
                "kind": "rehearsal",
                "candidate_id": "C-21-123-1",
                "rehearsal_id": "R-21-456-1",
                "main_sha": "a" * 40,
                "source_sha": "b" * 40,
                "integration_tree_sha": "c" * 40,
                "rehearsal_ref": "refs/tags/exp-rehearsal/21/R-21-456-1",
            },
        ]
        api.return_value = {"object": {"sha": "9" * 40}}
        with self.assertRaisesRegex(IntegrationControlError, "stale"):
            prepare(argparse.Namespace(repo="owner/repo", experiment_id="EXP-21"))

    @patch("integration_control.github_json")
    @patch("integration_control.github_content_json")
    def test_finalize_verifies_merged_pr_tree_and_main_ancestry(self, content, api):
        content.side_effect = [
            {
                "experiment_id": "EXP-21",
                "lifecycle": "SELECTED",
                "current_candidate_id": "C-21-123-1",
                "current_rehearsal_id": "R-21-456-1",
            },
            {
                "kind": "rehearsal",
                "candidate_id": "C-21-123-1",
                "rehearsal_id": "R-21-456-1",
                "main_sha": "a" * 40,
                "source_sha": "b" * 40,
                "integration_tree_sha": "c" * 40,
            },
        ]
        api.side_effect = [
            {
                "id": 9001,
                "html_url": "https://github.com/owner/repo/pull/77",
                "merged": True,
                "merged_at": "2026-09-24T16:30:00Z",
                "merged_by": {"login": "reviewer", "id": 101},
                "base": {"ref": "main"},
                "head": {
                    "ref": "game-exp/integration/21/R-21-456-1",
                    "sha": "d" * 40,
                    "repo": {"full_name": "owner/repo"},
                },
                "merge_commit_sha": "e" * 40,
            },
            {
                "tree": {"sha": "c" * 40},
                "parents": [{"sha": "a" * 40}],
            },
            {"tree": {"sha": "c" * 40}, "parents": []},
            {
                "status": "ahead",
                "merge_base_commit": {"sha": "e" * 40},
            },
        ]
        out = finalize(
            argparse.Namespace(
                repo="owner/repo",
                experiment_id="EXP-21",
                pr_number="77",
                workflow_source_sha="f" * 40,
                run_id="789",
                run_attempt="1",
            )
        )
        self.assertEqual(out["integration_id"], "I-21-PR-77")
        self.assertEqual(out["head_tree_sha"], "c" * 40)
        self.assertEqual(out["merge_tree_sha"], "c" * 40)
        self.assertEqual(out["merged_by_login"], "reviewer")

    @patch("integration_control.github_json")
    @patch("integration_control.github_content_json")
    def test_finalize_rejects_wrong_merge_tree(self, content, api):
        content.side_effect = [
            {
                "experiment_id": "EXP-21",
                "lifecycle": "SELECTED",
                "current_candidate_id": "C-21-123-1",
                "current_rehearsal_id": "R-21-456-1",
            },
            {
                "kind": "rehearsal",
                "candidate_id": "C-21-123-1",
                "rehearsal_id": "R-21-456-1",
                "main_sha": "a" * 40,
                "source_sha": "b" * 40,
                "integration_tree_sha": "c" * 40,
            },
        ]
        api.side_effect = [
            {
                "id": 9001,
                "html_url": "https://github.com/owner/repo/pull/77",
                "merged": True,
                "merged_at": "2026-09-24T16:30:00Z",
                "merged_by": {"login": "reviewer", "id": 101},
                "base": {"ref": "main"},
                "head": {
                    "ref": "game-exp/integration/21/R-21-456-1",
                    "sha": "d" * 40,
                    "repo": {"full_name": "owner/repo"},
                },
                "merge_commit_sha": "e" * 40,
            },
            {
                "tree": {"sha": "c" * 40},
                "parents": [{"sha": "a" * 40}],
            },
            {"tree": {"sha": "9" * 40}, "parents": []},
        ]
        with self.assertRaisesRegex(IntegrationControlError, "tree differs"):
            finalize(
                argparse.Namespace(
                    repo="owner/repo",
                    experiment_id="EXP-21",
                    pr_number="77",
                    workflow_source_sha="f" * 40,
                    run_id="789",
                    run_attempt="1",
                )
            )

    def test_payload_is_stable_and_binds_pr(self):
        context = {
            "experiment_id": "EXP-21",
            "candidate_id": "C-21-123-1",
            "rehearsal_id": "R-21-456-1",
            "integration_id": "I-21-PR-77",
            "pr_number": "77",
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "context.json"
            path.write_text(json.dumps(context), encoding="utf-8")
            out = request_payload(argparse.Namespace(context_json=str(path)))
        payload = decode_payload_b64(out["payload_b64"])
        self.assertEqual(out["request_id"], "req-integration-21-pr-77")
        self.assertEqual(payload["operation"], "integration.register")
        self.assertEqual(payload["input"], {"experiment_id": "EXP-21"})
        self.assertEqual(payload["preconditions"]["pr_number"], "77")


if __name__ == "__main__":
    unittest.main()
