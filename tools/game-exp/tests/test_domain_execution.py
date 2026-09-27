from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))

from domain_core import DomainError, TrustedActorContext, plan_domain_mutation  # noqa: E402
from protocol_core import build_operation_payload, digest_object  # noqa: E402


class AsyncExecutionClaimTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.experiment_id = "EXP-9"
        self.state = {
            "kind": "experiment_state",
            "experiment_id": self.experiment_id,
            "lifecycle": "REVIEW",
            "sequence": 2,
            "last_decision_id": "req-review-state",
            "archive_lock": None,
            "created_by_request_id": "req_bind_9",
            "current_candidate_id": None,
        }
        manifest = {
            "schema_version": 1,
            "experiment": {
                "host": "github.com",
                "repository_id": "1",
                "issue_id": "9",
                "issue_number": "9",
            },
            "title": "Test",
            "operation_id": "req_bind_9",
            "parent": {"experiment": None, "commit": "a" * 40},
            "hypothesis": "test",
            "success_criteria": ["works"],
            "kill_criteria": ["fails"],
            "scope": {"allowed": ["games/test/**"], "avoid": []},
            "runtime": {
                "godot": "4.5",
                "export_templates": "4.5",
                "addons_lock": "none",
            },
            "review": {"protocol": "blind-playtest-v1"},
            "created_at": "2026-09-27T00:00:00Z",
            "subject": {
                "type": "game-prototype",
                "id": "test-game",
                "name": "Test Game",
                "root_path": "games/test",
            },
        }
        bind_payload = build_operation_payload(
            "experiment.bind",
            {"manifest": manifest},
        )
        bind_digest = digest_object(bind_payload)
        root = self.root / "experiments" / self.experiment_id
        root.mkdir(parents=True)
        (root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False),
            encoding="utf-8",
        )
        binding = {
            "kind": "experiment_identity",
            "experiment_id": self.experiment_id,
            "request_id": "req_bind_9",
            "inputs_digest": bind_digest,
            "parent_sha": "a" * 40,
            "canonical": {
                "host": "github.com",
                "repository_id": "1",
                "issue_id": "9",
                "issue_number": "9",
            },
            "initialization": {
                "manifest_digest": digest_object(manifest),
            },
        }
        (root / "binding.json").write_text(json.dumps(binding), encoding="utf-8")
        (root / "state.json").write_text(json.dumps(self.state), encoding="utf-8")
        operations = self.root / "operations"
        operations.mkdir()
        operation = {
            "request_id": "req_bind_9",
            "payload": bind_payload,
            "payload_digest": bind_digest,
            "domain_status": "APPLIED",
            "domain_experiment_id": self.experiment_id,
            "domain_paths": [
                f"experiments/{self.experiment_id}/binding.json",
                f"experiments/{self.experiment_id}/manifest.json",
                f"experiments/{self.experiment_id}/state.json",
            ],
        }
        (operations / "req_bind_9.json").write_text(
            json.dumps(operation),
            encoding="utf-8",
        )
        self.actor = TrustedActorContext(
            login="alice",
            user_id="1001",
            permission="write",
        )

    def plan(self, *, action="candidate_build", arguments=None, state_digest=None, actor=None):
        payload = build_operation_payload(
            "execution.claim",
            {
                "experiment_id": self.experiment_id,
                "action": action,
                "arguments": arguments or {},
                "state_digest": state_digest or digest_object(self.state),
            },
        )
        return plan_domain_mutation(
            repo_dir=self.root,
            payload=payload,
            request_id="req_exec_9",
            payload_digest=digest_object(payload),
            repository_full_name="owner/repo",
            trusted_actor=self.actor if actor is None else actor,
        )

    def test_claim_is_request_only_and_bound_to_experiment(self):
        plan = self.plan()
        self.assertEqual(plan.status, "REQUEST_ONLY")
        self.assertEqual(plan.experiment_id, self.experiment_id)
        self.assertEqual(plan.writes, {})

    def test_claim_rejects_stale_state(self):
        with self.assertRaises(DomainError) as ctx:
            self.plan(state_digest="sha256:" + "0" * 64)
        self.assertEqual(ctx.exception.code, "DOMAIN_EXECUTION_CONFLICT")

    def test_claim_rejects_read_only_actor(self):
        with self.assertRaises(DomainError) as ctx:
            self.plan(
                actor=TrustedActorContext(
                    login="reader",
                    user_id="1002",
                    permission="read",
                )
            )
        self.assertEqual(ctx.exception.code, "DOMAIN_AUTHORIZATION_FAILED")

    def test_claim_rejects_wrong_lifecycle(self):
        with self.assertRaises(DomainError) as ctx:
            self.plan(action="rehearse")
        self.assertEqual(ctx.exception.code, "DOMAIN_EXECUTION_CONFLICT")

    def test_claim_validates_action_arguments(self):
        with self.assertRaises(DomainError) as ctx:
            self.plan(action="archive", arguments={"mode": "DELETE"})
        self.assertEqual(ctx.exception.code, "DOMAIN_EXECUTION_INVALID")


if __name__ == "__main__":
    unittest.main()
