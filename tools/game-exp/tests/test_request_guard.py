from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))

import request_guard  # noqa: E402
from protocol_core import (  # noqa: E402
    build_operation_payload,
    digest_object,
    encode_payload_b64,
)


class RequestGuardTests(unittest.TestCase):
    def payload(self, value="one"):
        payload = build_operation_payload(
            "experiment.decision",
            {"experiment_id": "EXP-9", "value": value},
        )
        return payload, encode_payload_b64(payload), digest_object(payload)

    def test_first_payload_digest_and_head_define_request_identity(self):
        _payload, encoded, digest = self.payload()
        head = "a" * 40
        runs = {
            "workflow_runs": [
                {
                    "id": 100,
                    "display_title": f"game-exp:request:req_shared_9:{digest}:{head}",
                },
                {
                    "id": 101,
                    "display_title": f"game-exp:request:req_shared_9:{digest}:{head}",
                },
            ]
        }
        with patch("request_guard.github_json", return_value=runs):
            result = request_guard.decide_request_identity(
                repo="owner/repo",
                workflow="game-exp-trusted-writer.yml",
                request_id="req_shared_9",
                payload_b64=encoded,
                payload_digest=digest,
                expected_head=head,
                run_id="101",
            )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["first_run_id"], "100")
        self.assertEqual(result["expected_head"], head)

    def test_same_request_id_different_payload_is_conflict_before_ledger_commit(self):
        _payload, encoded, digest = self.payload("new")
        first_digest = "sha256:" + "1" * 64
        head = "a" * 40
        runs = {
            "workflow_runs": [
                {
                    "id": 100,
                    "display_title": (
                        f"game-exp:request:req_shared_9:{first_digest}:{head}"
                    ),
                },
                {
                    "id": 101,
                    "display_title": f"game-exp:request:req_shared_9:{digest}:{head}",
                },
            ]
        }
        with patch("request_guard.github_json", return_value=runs):
            result = request_guard.decide_request_identity(
                repo="owner/repo",
                workflow="game-exp-trusted-writer.yml",
                request_id="req_shared_9",
                payload_b64=encoded,
                payload_digest=digest,
                expected_head=head,
                run_id="101",
            )
        self.assertEqual(result["status"], "CONFLICT")
        self.assertEqual(result["conflict_type"], "REQUEST_ID_CONFLICT")
        self.assertEqual(result["first_payload_digest"], first_digest)

    def test_same_payload_with_different_original_head_is_conflict(self):
        _payload, encoded, digest = self.payload()
        first_head = "a" * 40
        new_head = "b" * 40
        runs = {
            "workflow_runs": [
                {
                    "id": 100,
                    "display_title": (
                        f"game-exp:request:req_shared_9:{digest}:{first_head}"
                    ),
                },
                {
                    "id": 101,
                    "display_title": (
                        f"game-exp:request:req_shared_9:{digest}:{new_head}"
                    ),
                },
            ]
        }
        with patch("request_guard.github_json", return_value=runs):
            result = request_guard.decide_request_identity(
                repo="owner/repo",
                workflow="game-exp-trusted-writer.yml",
                request_id="req_shared_9",
                payload_b64=encoded,
                payload_digest=digest,
                expected_head=new_head,
                run_id="101",
            )
        self.assertEqual(result["status"], "CONFLICT")
        self.assertEqual(result["first_expected_head"], first_head)
        self.assertEqual(result["new_expected_head"], new_head)

    def test_claimed_digest_must_match_actual_payload(self):
        _payload, encoded, _digest = self.payload()
        with self.assertRaisesRegex(
            request_guard.RequestGuardError,
            "payload digest mismatch",
        ):
            request_guard.decide_request_identity(
                repo="owner/repo",
                workflow="game-exp-trusted-writer.yml",
                request_id="req_shared_9",
                payload_b64=encoded,
                payload_digest="sha256:" + "0" * 64,
                expected_head="a" * 40,
                run_id="100",
            )


if __name__ == "__main__":
    unittest.main()
