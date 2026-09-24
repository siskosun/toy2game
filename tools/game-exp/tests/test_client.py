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
        self._ledger_json = {}
        self._ledger_paths = []
        self._ledger_json_refs = []
        self._git_refs = {}
        self._tag_objects = {}
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

    def dispatch_candidate(self, experiment_id):
        self.dispatched.append({"candidate": experiment_id})
        if self.dispatch_uncertain:
            raise TransportUncertainError("network outcome unknown")
        return "https://github.com/owner/repo/actions/runs/788"

    def dispatch_rehearsal(self, experiment_id):
        self.dispatched.append({"rehearsal": experiment_id})
        if self.dispatch_uncertain:
            raise TransportUncertainError("network outcome unknown")
        return "https://github.com/owner/repo/actions/runs/789"

    def dispatch_integration(self, experiment_id):
        self.dispatched.append({"integration": experiment_id})
        if self.dispatch_uncertain:
            raise TransportUncertainError("network outcome unknown")
        return "https://github.com/owner/repo/actions/runs/790"

    def dispatch_integration_finalize(self, experiment_id, pr_number):
        self.dispatched.append(
            {"integration_finalize": experiment_id, "pr_number": str(pr_number)}
        )
        if self.dispatch_uncertain:
            raise TransportUncertainError("network outcome unknown")
        return "https://github.com/owner/repo/actions/runs/791"

    def dispatch_archive(self, experiment_id, mode):
        self.dispatched.append({"archive": experiment_id, "mode": mode})
        if self.dispatch_uncertain:
            raise TransportUncertainError("network outcome unknown")
        return "https://github.com/owner/repo/actions/runs/792"

    def ledger_paths(self, ref):
        self.last_ledger_paths_ref = ref
        return list(self._ledger_paths)

    def ledger_json(self, path, *, ref=None):
        self._ledger_json_refs.append((path, ref))
        return self._ledger_json.get(path)

    def git_ref(self, ref_path):
        return self._git_refs.get(ref_path)

    def annotated_tag(self, tag_object_sha):
        return self._tag_objects[tag_object_sha]

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

    def _add_valid_board_binding(
        self,
        transport: FakeTransport,
        experiment_id: str,
        manifest: dict,
    ):
        request_id = "req_bind_" + experiment_id.lower().replace("-", "_")
        payload = {
            "kind": "operation_request",
            "schema_version": 1,
            "operation": "experiment.bind",
            "input": {"manifest": manifest},
            "preconditions": {},
        }
        digest = digest_object(payload)
        transport._ledger_json[f"experiments/{experiment_id}/binding.json"] = {
            "request_id": request_id,
            "inputs_digest": digest,
            "initialization": {"manifest_digest": digest_object(manifest)},
        }
        transport._ledger_json[f"operations/{request_id}.json"] = {
            "request_id": request_id,
            "payload_digest": digest,
            "payload": payload,
        }
        return request_id

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

    def test_experiment_get_projects_current_domain_objects(self):
        transport = FakeTransport()
        transport._ledger_json["experiments/EXP-21/state.json"] = {
            "experiment_id": "EXP-21",
            "lifecycle": "ARCHIVED",
            "current_candidate_id": "C-21-1-1",
            "current_review_id": "req_review",
            "current_rehearsal_id": "R-21-2-1",
            "current_integration_id": "I-21-PR-3",
            "current_archive_id": "A-21-1",
        }
        transport._ledger_json["experiments/EXP-21/binding.json"] = {"kind": "experiment_identity"}
        transport._ledger_json["experiments/EXP-21/manifest.json"] = {"schema_version": 1}
        transport._ledger_json["experiments/EXP-21/candidates/C-21-1-1.json"] = {"candidate_id": "C-21-1-1"}
        transport._ledger_json["experiments/EXP-21/reviews/req_review.json"] = {"review_id": "req_review"}
        transport._ledger_json["experiments/EXP-21/rehearsals/R-21-2-1.json"] = {"rehearsal_id": "R-21-2-1"}
        transport._ledger_json["experiments/EXP-21/integrations/I-21-PR-3.json"] = {"integration_id": "I-21-PR-3"}
        transport._ledger_json["experiments/EXP-21/archives/A-21-1.json"] = {"archive_id": "A-21-1"}

        result = GameExpClient(transport).experiment_get("EXP-21")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["state"]["lifecycle"], "ARCHIVED")
        self.assertEqual(result["candidate"]["candidate_id"], "C-21-1-1")
        self.assertEqual(result["review"]["review_id"], "req_review")
        self.assertEqual(result["rehearsal"]["rehearsal_id"], "R-21-2-1")
        self.assertEqual(result["integration"]["integration_id"], "I-21-PR-3")
        self.assertEqual(result["archive"]["archive_id"], "A-21-1")

    def test_experiment_get_returns_unknown_for_missing_experiment(self):
        result = GameExpClient(FakeTransport()).experiment_get("EXP-99")
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["reason"], "experiment_not_found_in_ledger")

    def test_board_projects_consistent_lightweight_snapshot(self):
        transport = FakeTransport()
        transport._ledger_paths = [
            "experiments/EXP-7/state.json",
            "experiments/EXP-7/manifest.json",
            "experiments/EXP-21/state.json",
            "experiments/EXP-21/manifest.json",
            "experiments/EXP-21/reviews/req_review.json",
            "operations/req_other.json",
        ]
        transport._ledger_json.update(
            {
                "experiments/EXP-7/state.json": {
                    "experiment_id": "EXP-7",
                    "lifecycle": "REVIEW",
                    "current_candidate_id": "C-7-1-1",
                    "current_review_id": None,
                    "archive_lock": None,
                },
                "experiments/EXP-7/manifest.json": {
                    "title": "Combat readability",
                    "subject": {
                        "type": "game-prototype",
                        "id": "arena-duel",
                        "name": "Arena Duel",
                        "root_path": "games/arena-duel",
                    },
                    "hypothesis": "roles improve readability",
                    "experiment": {"issue_number": "7"},
                    "scope": {"allowed": ["games/arena-duel/**"]},
                    "created_at": "2026-09-24T10:00:00Z",
                },
                "experiments/EXP-21/state.json": {
                    "experiment_id": "EXP-21",
                    "lifecycle": "ARCHIVED",
                    "current_candidate_id": "C-21-1-1",
                    "current_review_id": "req_review",
                    "current_rehearsal_id": "R-21-2-1",
                    "current_integration_id": "I-21-PR-3",
                    "current_archive_id": "A-21-1",
                    "archive_lock": None,
                },
                "experiments/EXP-21/manifest.json": {
                    "title": "Binding pilot",
                    "hypothesis": "trusted binding works",
                    "experiment": {"issue_number": "21"},
                    "scope": {"allowed": ["games/**"]},
                    "created_at": "2026-09-24T11:00:00Z",
                },
                "experiments/EXP-21/reviews/req_review.json": {
                    "review_id": "req_review",
                    "outcome": "PASS",
                },
            }
        )

        self._add_valid_board_binding(
            transport,
            "EXP-7",
            transport._ledger_json["experiments/EXP-7/manifest.json"],
        )
        self._add_valid_board_binding(
            transport,
            "EXP-21",
            transport._ledger_json["experiments/EXP-21/manifest.json"],
        )

        result = GameExpClient(transport).board()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["snapshot_head"], transport.head)
        self.assertEqual(result["count"], 2)
        self.assertEqual(
            result["counts_by_lifecycle"],
            {"ARCHIVED": 1, "REVIEW": 1},
        )
        self.assertEqual(result["counts_by_health"], {"PASS": 2})
        self.assertEqual(
            [row["experiment_id"] for row in result["experiments"]],
            ["EXP-7", "EXP-21"],
        )
        self.assertEqual(
            result["experiments"][0]["next_gate"],
            "HUMAN_REVIEW",
        )
        self.assertEqual(
            result["experiments"][1]["next_gate"],
            "TERMINAL_NEW_EXPERIMENT_FOR_NEW_WORK",
        )
        self.assertEqual(result["experiments"][1]["review_outcome"], "PASS")
        self.assertEqual(result["repo"], "owner/repo")
        self.assertEqual(result["repository_name"], "repo")
        self.assertEqual(result["experiments"][0]["repository"], "owner/repo")
        self.assertEqual(result["experiments"][0]["repository_name"], "repo")
        self.assertEqual(result["experiments"][0]["prototype_name"], "Arena Duel")
        self.assertEqual(result["experiments"][0]["subject_id"], "arena-duel")
        self.assertEqual(result["experiments"][0]["subject_source"], "manifest")
        self.assertEqual(result["experiments"][1]["prototype_name"], "仓库级/未指定原型")
        self.assertEqual(result["experiments"][1]["subject_source"], "scope-fallback")
        self.assertEqual(result["attention_count"], 1)
        self.assertEqual(
            result["views"]["overview"],
            {
                "attention_ids": ["EXP-7"],
                "active_ids": ["EXP-7"],
                "archived_count": 1,
            },
        )
        self.assertEqual(
            result["views"]["attention"]["experiment_ids"],
            ["EXP-7"],
        )
        self.assertEqual(
            result["views"]["prototypes"]["groups"][0]["subject"]["name"],
            "Arena Duel",
        )
        self.assertEqual(
            result["views"]["prototypes"]["groups"][0]["attention_count"],
            1,
        )
        self.assertEqual(
            result["views"]["branches"]["lanes"][0]["experiment_id"],
            "EXP-7",
        )
        self.assertEqual(
            result["views"]["archive"]["experiment_ids"],
            ["EXP-21"],
        )
        self.assertEqual(result["experiments"][0]["health"], "PASS")
        self.assertEqual(result["experiments"][1]["health"], "PASS")
        self.assertEqual(transport.last_ledger_paths_ref, transport.head)
        self.assertTrue(transport._ledger_json_refs)
        self.assertTrue(
            all(ref == transport.head for _path, ref in transport._ledger_json_refs)
        )

    def test_board_marks_archive_lock_as_recovery_gate(self):
        transport = FakeTransport()
        transport._ledger_paths = ["experiments/EXP-9/state.json"]
        transport._ledger_json.update(
            {
                "experiments/EXP-9/state.json": {
                    "experiment_id": "EXP-9",
                    "lifecycle": "INTEGRATED",
                    "archive_lock": {"archive_id": "A-9-1", "phase": "CLAIMED"},
                },
                "experiments/EXP-9/manifest.json": {
                    "title": "Archive recovery",
                    "hypothesis": "recover same archive",
                    "experiment": {"issue_number": "9"},
                },
            }
        )
        self._add_valid_board_binding(
            transport,
            "EXP-9",
            transport._ledger_json["experiments/EXP-9/manifest.json"],
        )
        result = GameExpClient(transport).board()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["experiments"][0]["health"], "PASS")
        self.assertEqual(result["experiments"][0]["next_gate"], "ARCHIVE_RECOVERY")

    def test_board_blocks_corrupted_binding_request_digest(self):
        transport = FakeTransport()
        manifest = {
            "title": "Corrupt pilot",
            "hypothesis": "must not be actionable",
            "experiment": {"issue_number": "19"},
        }
        transport._ledger_paths = [
            "experiments/EXP-19/state.json",
            "experiments/EXP-19/manifest.json",
            "experiments/EXP-19/binding.json",
        ]
        transport._ledger_json["experiments/EXP-19/state.json"] = {
            "experiment_id": "EXP-19",
            "lifecycle": "ACTIVE",
            "archive_lock": None,
        }
        transport._ledger_json["experiments/EXP-19/manifest.json"] = manifest
        request_id = self._add_valid_board_binding(transport, "EXP-19", manifest)
        transport._ledger_json[f"operations/{request_id}.json"]["payload"]["input"][
            "manifest"
        ]["title"] = "mutated after digest"

        result = GameExpClient(transport).board()
        row = result["experiments"][0]
        self.assertEqual(row["health"], "FAIL")
        self.assertEqual(
            row["health_code"],
            "BINDING_REQUEST_PAYLOAD_DIGEST_MISMATCH",
        )
        self.assertEqual(row["next_gate"], "DO_NOT_USE_RECREATE_EXPERIMENT")
        self.assertTrue(row["attention"]["required"])
        self.assertEqual(row["attention"]["priority"], 0)
        self.assertEqual(row["attention"]["reason"], "HEALTH_FAIL")
        self.assertEqual(result["views"]["attention"]["experiment_ids"], ["EXP-19"])
        self.assertEqual(result["counts_by_health"], {"FAIL": 1})

    def test_candidate_dispatches_trusted_workflow(self):
        transport = FakeTransport()
        result = GameExpClient(transport).candidate("EXP-21")
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertEqual(transport.dispatched[-1], {"candidate": "EXP-21"})

    def test_candidate_uncertain_dispatch_is_not_claimed_retry_safe(self):
        transport = FakeTransport()
        transport.dispatch_uncertain = True
        result = GameExpClient(transport).candidate("EXP-21")
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertFalse(result["retry_safe"])

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

    def test_rehearse_dispatches_only_canonical_experiment_id(self):
        transport = FakeTransport()
        result = GameExpClient(transport).rehearse("EXP-42")
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertEqual(result["experiment_id"], "EXP-42")
        self.assertEqual(transport.dispatched[-1], {"rehearsal": "EXP-42"})

    def test_rehearse_rejects_noncanonical_id_before_dispatch(self):
        transport = FakeTransport()
        result = GameExpClient(transport).rehearse("exp/42")
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(transport.dispatched, [])

    def test_rehearse_uncertain_dispatch_is_retry_safe(self):
        transport = FakeTransport()
        transport.dispatch_uncertain = True
        result = GameExpClient(transport).rehearse("EXP-42")
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertTrue(result["retry_safe"])

    def test_integrate_dispatches_proposal_workflow(self):
        transport = FakeTransport()
        result = GameExpClient(transport).integrate("EXP-21")
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertEqual(transport.dispatched[-1], {"integration": "EXP-21"})

    def test_integrate_finalize_dispatches_merged_pr_verifier(self):
        transport = FakeTransport()
        result = GameExpClient(transport).integrate_finalize("EXP-21", "77")
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertEqual(
            transport.dispatched[-1],
            {"integration_finalize": "EXP-21", "pr_number": "77"},
        )

    def test_integrate_finalize_rejects_invalid_pr_number(self):
        result = GameExpClient(FakeTransport()).integrate_finalize("EXP-21", "0")
        self.assertEqual(result["status"], "REJECTED")

    def test_archive_dispatches_trusted_workflow(self):
        transport = FakeTransport()
        result = GameExpClient(transport).archive("EXP-21", "ATOMIC_DELETE")
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertEqual(
            transport.dispatched[-1],
            {"archive": "EXP-21", "mode": "ATOMIC_DELETE"},
        )

    def test_archive_rejects_invalid_mode_before_dispatch(self):
        transport = FakeTransport()
        result = GameExpClient(transport).archive("EXP-21", "DELETE")
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(transport.dispatched, [])

    def test_archive_uncertain_dispatch_is_retry_safe(self):
        transport = FakeTransport()
        transport.dispatch_uncertain = True
        result = GameExpClient(transport).archive("EXP-21", "RETAIN_BRANCH")
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertTrue(result["retry_safe"])

    def test_archive_abort_submits_human_gated_request(self):
        transport = FakeTransport()
        client = GameExpClient(transport)
        result = client.archive_abort(
            "EXP-21",
            "A-21-1",
            "cancel before claim",
            actor_claim="reviewer",
            request_id="req_archive_abort_test",
        )
        self.assertEqual(result["status"], "ACCEPTED")
        sent = transport.dispatched[-1]
        self.assertEqual(sent["request_id"], "req_archive_abort_test")
        self.assertIn("payload_b64", sent)

    def _archive_fixture(self, transport, *, mode, branch_sha):
        experiment_id = "EXP-21"
        archive_id = "A-21-1"
        expected = "b" * 40
        transport._ledger_json[f"experiments/{experiment_id}/state.json"] = {
            "kind": "experiment_state",
            "experiment_id": experiment_id,
            "lifecycle": "ARCHIVED",
            "current_archive_id": archive_id,
        }
        transport._ledger_json[
            f"experiments/{experiment_id}/archives/{archive_id}.json"
        ] = {
            "kind": "archive_operation",
            "archive_id": archive_id,
            "experiment_id": experiment_id,
            "phase": "COMMITTED",
            "mode": mode,
            "expected_branch_sha": expected,
            "branch_ref": "refs/heads/exp/21",
            "final_tag_ref": "refs/tags/exp-final/21",
        }
        transport._git_refs["tags/exp-final/21"] = {
            "object": {"type": "tag", "sha": "1" * 40}
        }
        transport._tag_objects["1" * 40] = {
            "object": {"type": "commit", "sha": expected},
            "message": "\n".join(
                [
                    "game-exp archive A-21-1",
                    "",
                    "game-exp-experiment: EXP-21",
                    "game-exp-archive-id: A-21-1",
                    f"game-exp-mode: {mode}",
                    f"game-exp-source-sha: {expected}",
                ]
            ),
        }
        if branch_sha is not None:
            transport._git_refs["heads/exp/21"] = {
                "object": {"type": "commit", "sha": branch_sha}
            }

    def test_archive_health_atomic_delete_passes_with_branch_absent(self):
        transport = FakeTransport()
        self._archive_fixture(transport, mode="ATOMIC_DELETE", branch_sha=None)
        result = GameExpClient(transport).archive_health("EXP-21")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["code"], "ARCHIVE_HEALTHY")

    def test_archive_health_retain_branch_drift_is_warning(self):
        transport = FakeTransport()
        self._archive_fixture(transport, mode="RETAIN_BRANCH", branch_sha="c" * 40)
        result = GameExpClient(transport).archive_health("EXP-21")
        self.assertEqual(result["status"], "WARN")
        self.assertEqual(result["code"], "POST_ARCHIVE_BRANCH_DRIFT")
        self.assertEqual(result["official_snapshot_sha"], "b" * 40)
        self.assertEqual(result["branch_sha"], "c" * 40)

    def test_archive_health_missing_final_tag_is_failure(self):
        transport = FakeTransport()
        self._archive_fixture(transport, mode="ATOMIC_DELETE", branch_sha=None)
        transport._git_refs.pop("tags/exp-final/21")
        result = GameExpClient(transport).archive_health("EXP-21")
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["code"], "ARCHIVE_FINAL_TAG_MISSING")

    def test_doctor_surfaces_archive_warning_without_overwriting_snapshot(self):
        transport = FakeTransport()
        self._archive_fixture(transport, mode="RETAIN_BRANCH", branch_sha="c" * 40)
        result = GameExpClient(transport).doctor("EXP-21")
        self.assertEqual(result["status"], "WARN")
        archive = next(row for row in result["checks"] if row["name"] == "archive_health")
        self.assertEqual(archive["status"], "WARN")
        self.assertEqual(archive["detail"]["code"], "POST_ARCHIVE_BRANCH_DRIFT")

    def test_doctor_skips_archive_health_for_unbound_experiment(self):
        result = GameExpClient(FakeTransport()).doctor("EXP-50")
        self.assertEqual(result["status"], "PASS")
        archive = next(row for row in result["checks"] if row["name"] == "archive_health")
        self.assertEqual(archive["status"], "SKIP")
        self.assertEqual(archive["detail"]["code"], "EXPERIMENT_NOT_BOUND")

    def test_doctor_pass(self):
        result = GameExpClient(FakeTransport()).doctor()
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(all(row["status"] == "PASS" for row in result["checks"]))


if __name__ == "__main__":
    unittest.main()
