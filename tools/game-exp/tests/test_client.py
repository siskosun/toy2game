from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))

from client import GameExpClient  # noqa: E402
from protocol_core import digest_object  # noqa: E402


class FakeTransport:
    repo = "owner/repo"

    def __init__(self):
        self.head = "a" * 40
        self.dispatched = []
        self.record = None
        self.state = None
        self.logs = ""
        self._rules = [
            {"name": "game-exp ledger", "enforcement": "active"},
            {"name": "game-exp experiment branches", "enforcement": "active"},
            {"name": "game-exp immutable refs", "enforcement": "active"},
            {"name": "game-exp protected main", "enforcement": "active"},
        ]

    def ledger_head(self):
        return self.head

    def dispatch_writer(self, **kwargs):
        self.dispatched.append(kwargs)
        return "https://github.com/owner/repo/actions/runs/123"

    def ledger_record(self, request_id):
        return self.record

    def run_state(self, workflow_url):
        return self.state

    def failed_run_logs(self, workflow_url):
        return self.logs

    def rulesets(self):
        return self._rules

    def deploy_keys(self):
        return [{"title": "game-exp trusted writer", "read_only": False}]

    def secret_names(self):
        return {"GAME_EXP_WRITER_KEY"}

    def immutable_releases(self):
        return {"enabled": True, "enforced_by_owner": False}


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.patch = patch("client._journal_root", return_value=self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_submit_then_reconcile_committed(self):
        transport = FakeTransport()
        client = GameExpClient(transport)
        result = client.submit(
            operation="experiment.create",
            input_value={"hypothesis": "three roles"},
            request_id="req_test_1",
        )
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertEqual(len(transport.dispatched), 1)

        payload = {
            "kind": "operation_request",
            "schema_version": 1,
            "operation": "experiment.create",
            "input": {"hypothesis": "three roles"},
            "preconditions": {},
        }
        transport.record = {
            "request_id": "req_test_1",
            "payload_digest": digest_object(payload),
            "payload": payload,
        }
        reconciled = client.reconcile("req_test_1")
        self.assertEqual(reconciled["status"], "COMMITTED")
        self.assertTrue(reconciled["verified_against_local_request"])

    def test_remote_digest_mismatch_is_conflict(self):
        transport = FakeTransport()
        client = GameExpClient(transport)
        client.submit(
            operation="experiment.create",
            input_value={"hypothesis": "three roles"},
            request_id="req_test_2",
        )
        transport.record = {
            "request_id": "req_test_2",
            "payload_digest": "sha256:" + "0" * 64,
        }
        reconciled = client.reconcile("req_test_2")
        self.assertEqual(reconciled["status"], "CONFLICT")
        self.assertEqual(reconciled["conflict_type"], "REQUEST_ID_CONFLICT")

    def test_failed_workflow_maps_head_conflict(self):
        transport = FakeTransport()
        client = GameExpClient(transport)
        client.submit(
            operation="experiment.create",
            input_value={},
            request_id="req_test_3",
        )
        transport.state = {
            "status": "completed",
            "conclusion": "failure",
            "url": "https://github.com/owner/repo/actions/runs/123",
        }
        transport.logs = '{"status":"HEAD_CONFLICT"}'
        reconciled = client.reconcile("req_test_3")
        self.assertEqual(reconciled["status"], "CONFLICT")
        self.assertEqual(reconciled["conflict_type"], "HEAD_CONFLICT")

    def test_doctor_pass(self):
        result = GameExpClient(FakeTransport()).doctor()
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(all(row["status"] == "PASS" for row in result["checks"]))


if __name__ == "__main__":
    unittest.main()
