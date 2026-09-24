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

from rehearsal_control import (  # noqa: E402
    RehearsalControlError,
    payload,
    prepare,
    rehearsal_policy_digest,
)
from protocol_core import decode_payload_b64  # noqa: E402


class RehearsalControlTests(unittest.TestCase):
    @patch("rehearsal_control.github_json")
    @patch("rehearsal_control.github_content_json")
    def test_prepare_uses_ledger_candidate_and_current_main(self, content, api):
        content.side_effect = [
            {
                "kind": "experiment_state",
                "experiment_id": "EXP-21",
                "lifecycle": "PROMISING",
                "current_candidate_id": "C-21-123-1",
            },
            {
                "kind": "candidate",
                "experiment_id": "EXP-21",
                "candidate_id": "C-21-123-1",
                "source_sha": "b" * 40,
            },
        ]
        api.return_value = {"object": {"sha": "a" * 40}}
        args = argparse.Namespace(
            repo="owner/repo",
            experiment_id="EXP-21",
            run_id="456",
            run_attempt="1",
            workflow_source_sha="c" * 40,
        )
        result = prepare(args)
        self.assertEqual(result["candidate_id"], "C-21-123-1")
        self.assertEqual(result["source_sha"], "b" * 40)
        self.assertEqual(result["main_sha"], "a" * 40)
        self.assertEqual(result["rehearsal_id"], "R-21-456-1")
        self.assertEqual(
            result["rehearsal_ref"],
            "refs/tags/exp-rehearsal/21/R-21-456-1",
        )
        self.assertEqual(result["policy_digest"], rehearsal_policy_digest())

    @patch("rehearsal_control.github_json")
    @patch("rehearsal_control.github_content_json")
    def test_prepare_rejects_non_promising_experiment(self, content, api):
        content.side_effect = [
            {
                "kind": "experiment_state",
                "experiment_id": "EXP-21",
                "lifecycle": "REVIEW",
                "current_candidate_id": "C-21-123-1",
            }
        ]
        args = argparse.Namespace(
            repo="owner/repo",
            experiment_id="EXP-21",
            run_id="456",
            run_attempt="1",
            workflow_source_sha="c" * 40,
        )
        with self.assertRaisesRegex(RehearsalControlError, "PROMISING"):
            prepare(args)
        api.assert_not_called()

    def test_payload_binds_exact_rehearsal_context(self):
        with tempfile.TemporaryDirectory() as td:
            context_path = str(Path(td) / "context.json")
            args = argparse.Namespace(
                experiment_id="EXP-21",
                candidate_id="C-21-123-1",
                rehearsal_id="R-21-456-1",
                main_sha="a" * 40,
                source_sha="b" * 40,
                integration_sha="c" * 40,
                integration_tree_sha="d" * 40,
                rehearsal_ref="refs/tags/exp-rehearsal/21/R-21-456-1",
                workflow_source_sha="e" * 40,
                run_id="456",
                run_attempt="1",
                policy_digest="sha256:" + "f" * 64,
                context_output=context_path,
            )
            result = payload(args)
            request = decode_payload_b64(result["payload_b64"])
            context = json.loads(Path(context_path).read_text(encoding="utf-8"))
        self.assertEqual(result["request_id"], "req-rehearsal-456-1")
        self.assertEqual(request["operation"], "rehearsal.register")
        self.assertEqual(request["input"], {"experiment_id": "EXP-21"})
        self.assertEqual(
            request["preconditions"],
            {"candidate_id": "C-21-123-1", "main_sha": "a" * 40},
        )
        self.assertEqual(context["integration_sha"], "c" * 40)
        self.assertEqual(context["integration_tree_sha"], "d" * 40)
        self.assertEqual(
            {row["name"] for row in context["checks"]},
            {
                "merge",
                "project_tests",
                "build",
                "trusted_tree_recompute",
                "main_freshness",
            },
        )


if __name__ == "__main__":
    unittest.main()
