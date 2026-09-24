from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))

from client import GameExpClient, TransportUncertainError  # noqa: E402
from protocol_core import digest_object  # noqa: E402


class FakeTransport:
    repo = "owner/repo"

    def __init__(self):
        self.head = "a" * 40
        self.dispatched = []
        self.record = None
        self.state = None
        self.logs = ""
        self.dispatch_uncertain = False
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
        if self.dispatch_uncertain:
            raise TransportUncertainError("network outcome unknown")
        return "https://github.com/owner/repo/actions/runs/123"

    def dispatch_initializer(self, experiment_id):
        self.dispatched.append({"initializer": experiment_id})
        if self.dispatch_uncertain:
            raise TransportUncertainError("network outcome unknown")
        return "https://github.com/owner/repo/actions/runs/456"

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

    def test_submit_replay_returns_committed_without_second_dispatch(self):
        transport = FakeTransport()
        client = GameExpClient(transport)
        first = client.submit(
            operation="experiment.create",
            input_value={"hypothesis": "three roles"},
            request_id="req_test_replay",
        )
        self.assertEqual(first["status"], "ACCEPTED")
        self.assertEqual(len(transport.dispatched), 1)

        payload = {
            "kind": "operation_request",
            "schema_version": 1,
            "operation": "experiment.create",
            "input": {"hypothesis": "three roles"},
            "preconditions": {},
        }
        transport.record = {
            "request_id": "req_test_replay",
            "payload_digest": digest_object(payload),
            "payload": payload,
        }

        replay = client.submit(
            operation="experiment.create",
            input_value={"hypothesis": "three roles"},
            request_id="req_test_replay",
        )
        self.assertEqual(replay["status"], "COMMITTED")
        self.assertTrue(replay["replayed"])
        self.assertEqual(len(transport.dispatched), 1)

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


    def test_failed_workflow_maps_domain_identity_conflict(self):
        transport = FakeTransport()
        client = GameExpClient(transport)
        client.submit(
            operation="experiment.bind",
            input_value={"manifest": {"placeholder": True}},
            request_id="req_test_domain_conflict",
        )
        transport.state = {
            "status": "completed",
            "conclusion": "failure",
            "url": "https://github.com/owner/repo/actions/runs/123",
        }
        transport.logs = '{"status":"DOMAIN_IDENTITY_CONFLICT","error":"trusted identity mismatch"}'
        reconciled = client.reconcile("req_test_domain_conflict")
        self.assertEqual(reconciled["status"], "CONFLICT")
        self.assertEqual(reconciled["conflict_type"], "DOMAIN_IDENTITY_CONFLICT")

    def test_failed_workflow_maps_domain_invalid_to_rejected(self):
        transport = FakeTransport()
        client = GameExpClient(transport)
        client.submit(
            operation="experiment.bind",
            input_value={"manifest": {"placeholder": True}},
            request_id="req_test_domain_invalid",
        )
        transport.state = {
            "status": "completed",
            "conclusion": "failure",
            "url": "https://github.com/owner/repo/actions/runs/123",
        }
        transport.logs = '{"status":"DOMAIN_INVALID","error":"invalid manifest"}'
        reconciled = client.reconcile("req_test_domain_invalid")
        self.assertEqual(reconciled["status"], "REJECTED")
        self.assertEqual(reconciled["domain_error"], "DOMAIN_INVALID")

    def test_uncertain_dispatch_keeps_original_expected_head_for_retry(self):
        transport = FakeTransport()
        client = GameExpClient(transport)
        transport.dispatch_uncertain = True

        first = client.submit(
            operation="experiment.create",
            input_value={"hypothesis": "retry me"},
            request_id="req_test_uncertain",
        )
        self.assertEqual(first["status"], "UNKNOWN")
        original_head = first["expected_head"]

        transport.head = "b" * 40
        transport.dispatch_uncertain = False
        second = client.submit(
            operation="experiment.create",
            input_value={"hypothesis": "retry me"},
            request_id="req_test_uncertain",
        )
        self.assertEqual(second["status"], "ACCEPTED")
        self.assertEqual(transport.dispatched[-1]["expected_head"], original_head)

    def test_same_local_request_id_with_different_payload_is_conflict(self):
        transport = FakeTransport()
        client = GameExpClient(transport)
        client.submit(
            operation="experiment.create",
            input_value={"hypothesis": "A"},
            request_id="req_test_local_conflict",
        )
        result = client.submit(
            operation="experiment.create",
            input_value={"hypothesis": "B"},
            request_id="req_test_local_conflict",
        )
        self.assertEqual(result["status"], "CONFLICT")
        self.assertEqual(result["conflict_type"], "LOCAL_REQUEST_ID_CONFLICT")

    def test_status_is_explicit_pass(self):
        transport = FakeTransport()
        result = GameExpClient(transport).status()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["ledger_head"], transport.head)

    def test_initialize_dispatches_only_canonical_experiment_id(self):
        transport = FakeTransport()
        result = GameExpClient(transport).initialize("EXP-42")
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertEqual(result["experiment_id"], "EXP-42")
        self.assertEqual(transport.dispatched[-1], {"initializer": "EXP-42"})

    def test_initialize_rejects_noncanonical_id_before_dispatch(self):
        transport = FakeTransport()
        result = GameExpClient(transport).initialize("exp/42")
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(transport.dispatched, [])

    def test_initialize_uncertain_dispatch_is_retry_safe(self):
        transport = FakeTransport()
        transport.dispatch_uncertain = True
        result = GameExpClient(transport).initialize("EXP-42")
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertTrue(result["retry_safe"])

    def test_doctor_pass(self):
        result = GameExpClient(FakeTransport()).doctor()
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(all(row["status"] == "PASS" for row in result["checks"]))


if __name__ == "__main__":
    unittest.main()
