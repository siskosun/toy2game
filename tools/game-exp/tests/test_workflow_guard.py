from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))

import workflow_guard  # noqa: E402
from protocol_core import build_operation_payload, digest_object  # noqa: E402


class WorkflowGuardTests(unittest.TestCase):
    def record(self, *, permission="write", action="candidate_build", arguments=None):
        payload = build_operation_payload(
            "execution.claim",
            {
                "experiment_id": "EXP-9",
                "action": action,
                "arguments": arguments or {},
                "state_digest": "sha256:" + "1" * 64,
            },
        )
        return {
            "request_id": "req_exec_9",
            "payload": payload,
            "payload_digest": digest_object(payload),
            "domain_status": "REQUEST_ONLY",
            "domain_experiment_id": "EXP-9",
            "trusted_actor": {
                "login": "alice",
                "user_id": "1001",
                "permission": permission,
            },
        }

    def test_valid_claim_and_earliest_run_is_primary(self):
        record = self.record()
        runs = {
            "workflow_runs": [
                {
                    "id": 200,
                    "display_title": "game-exp:candidate_build:req_exec_9",
                },
                {
                    "id": 201,
                    "display_title": "game-exp:candidate_build:req_exec_9",
                },
            ]
        }
        with patch("workflow_guard.github_json", side_effect=[record, runs]):
            result = workflow_guard.decide_primary(
                repo="owner/repo",
                workflow="game-exp-candidate.yml",
                action="candidate_build",
                request_id="req_exec_9",
                run_id="200",
                experiment_id="EXP-9",
                arguments={},
            )
        self.assertTrue(result["primary"])
        self.assertEqual(result["primary_run_id"], "200")
        self.assertEqual(result["trusted_actor"]["actor_login"], "alice")

    def test_duplicate_run_is_not_primary(self):
        record = self.record()
        runs = {
            "workflow_runs": [
                {
                    "id": 200,
                    "display_title": "game-exp:candidate_build:req_exec_9",
                },
                {
                    "id": 201,
                    "display_title": "game-exp:candidate_build:req_exec_9",
                },
            ]
        }
        with patch("workflow_guard.github_json", side_effect=[record, runs]):
            result = workflow_guard.decide_primary(
                repo="owner/repo",
                workflow="game-exp-candidate.yml",
                action="candidate_build",
                request_id="req_exec_9",
                run_id="201",
                experiment_id="EXP-9",
                arguments={},
            )
        self.assertFalse(result["primary"])
        self.assertEqual(result["primary_run_id"], "200")

    def test_claim_argument_mismatch_is_rejected(self):
        with patch("workflow_guard.github_json", return_value=self.record()):
            with self.assertRaisesRegex(
                workflow_guard.WorkflowGuardError,
                "differs from trusted execution claim",
            ):
                workflow_guard.validate_execution_claim(
                    repo="owner/repo",
                    action="archive",
                    experiment_id="EXP-9",
                    request_id="req_exec_9",
                    arguments={"mode": "ATOMIC_DELETE"},
                )

    def test_read_only_claim_actor_is_rejected(self):
        with patch(
            "workflow_guard.github_json",
            return_value=self.record(permission="read"),
        ):
            with self.assertRaisesRegex(
                workflow_guard.WorkflowGuardError,
                "lacks write permission",
            ):
                workflow_guard.validate_execution_claim(
                    repo="owner/repo",
                    action="candidate_build",
                    experiment_id="EXP-9",
                    request_id="req_exec_9",
                    arguments={},
                )


if __name__ == "__main__":
    unittest.main()
