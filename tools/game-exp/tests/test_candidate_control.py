from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))

from candidate_control import candidate_payload, prepare_candidate  # noqa: E402
from domain_core import CANDIDATE_REQUIRED_CHECKS, candidate_policy_digest  # noqa: E402


def binding():
    return {
        "experiment_id": "EXP-42",
        "parent_sha": "a" * 40,
        "initialization": {
            "initialization_plan_digest": "sha256:" + "3" * 64,
            "manifest_digest": "sha256:" + "2" * 64,
        },
    }


def state(lifecycle="REVIEW"):
    return {
        "experiment_id": "EXP-42",
        "lifecycle": lifecycle,
        "archive_lock": None,
    }


def evidence(source_sha="c" * 40, compare_status="ahead"):
    return [
        {"object": {"type": "commit", "sha": source_sha}},
        {"object": {"type": "tag", "sha": "d" * 40}},
        {
            "object": {"type": "commit", "sha": "a" * 40},
            "message": (
                "game-exp base EXP-42\n\n"
                f"game-exp-initialization-commit: {'b' * 40}\n"
                f"game-exp-initialization-plan: {'sha256:' + '3' * 64}\n"
            ),
        },
        {"status": compare_status},
    ]


class CandidateControlTests(unittest.TestCase):
    @patch("candidate_control.github_json")
    @patch("candidate_control.load_authoritative_binding")
    def test_prepare_uses_authoritative_branch_and_base_tag(self, load, api):
        load.return_value = (binding(), {}, state(), {})
        api.side_effect = evidence()
        result = prepare_candidate(
            repo="owner/repo",
            experiment_id="EXP-42",
            run_id="123",
            run_attempt="1",
            workflow_source_sha="e" * 40,
        )
        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["candidate_id"], "C-42-123-1")
        self.assertEqual(result["source_sha"], "c" * 40)
        self.assertEqual(
            result["source_anchor_ref"],
            "refs/tags/exp-candidate/42/C-42-123-1",
        )
        self.assertEqual(result["release_tag"], "game-exp-candidate-123-1")
        self.assertEqual(result["policy_digest"], candidate_policy_digest())

    @patch("candidate_control.github_json")
    @patch("candidate_control.load_authoritative_binding")
    def test_prepare_rejects_non_review_state(self, load, api):
        load.return_value = (binding(), {}, state("ACTIVE"), {})
        with self.assertRaisesRegex(RuntimeError, "requires REVIEW or PROMISING"):
            prepare_candidate(
                repo="owner/repo",
                experiment_id="EXP-42",
                run_id="123",
                run_attempt="1",
                workflow_source_sha="e" * 40,
            )
        self.assertEqual(api.call_count, 0)

    @patch("candidate_control.github_json")
    @patch("candidate_control.load_authoritative_binding")
    def test_prepare_rejects_source_not_descended_from_initializer(self, load, api):
        load.return_value = (binding(), {}, state(), {})
        api.side_effect = evidence(compare_status="diverged")
        with self.assertRaisesRegex(RuntimeError, "not descended"):
            prepare_candidate(
                repo="owner/repo",
                experiment_id="EXP-42",
                run_id="123",
                run_attempt="1",
                workflow_source_sha="e" * 40,
            )

    def test_payload_uses_fixed_trusted_policy_checks(self):
        payload = candidate_payload(
            experiment_id="EXP-42",
            candidate_id="C-42-123-1",
            source_sha="c" * 40,
            source_anchor_ref="refs/tags/exp-candidate/42/C-42-123-1",
            manifest_digest="sha256:" + "2" * 64,
            artifact_digest="sha256:" + "1" * 64,
            workflow_source_sha="e" * 40,
            run_id="123",
            run_attempt="1",
            policy_digest=candidate_policy_digest(),
            release_tag="game-exp-candidate-123-1",
        )
        rows = payload["input"]["checks"]
        self.assertEqual([row["name"] for row in rows], list(CANDIDATE_REQUIRED_CHECKS))
        self.assertTrue(all(row["provenance"] == "TRUSTED_OBSERVED" for row in rows))
        self.assertTrue(all(row["status"] == "PASS" for row in rows))
        self.assertEqual(payload["actor_claim"], "trusted-candidate-workflow")


if __name__ == "__main__":
    unittest.main()
