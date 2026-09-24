from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))

from domain_core import (  # noqa: E402
    DomainError,
    TrustedActorContext,
    TrustedBindingContext,
    plan_domain_mutation,
)
from protocol_core import build_operation_payload, digest_object  # noqa: E402


def manifest(request_id="req_bind_42"):
    return {
        "schema_version": 1,
        "experiment": {
            "host": "github.com",
            "repository_id": "1384446218",
            "issue_id": "5000000042",
            "issue_number": "42",
        },
        "title": "Decision test",
        "operation_id": request_id,
        "parent": {"experiment": None, "commit": "a" * 40},
        "hypothesis": "Decision transitions are serialized.",
        "success_criteria": ["Only valid transitions commit."],
        "kill_criteria": ["A stale decision can overwrite a newer decision."],
        "scope": {"allowed": ["games/**"], "avoid": [".github/**"]},
        "runtime": {
            "godot": "n/a",
            "export_templates": "n/a",
            "addons_lock": "sha256:none",
        },
        "review": {"protocol": "blind-playtest-v1"},
        "created_at": "2026-09-24T14:00:00+08:00",
    }


class DecisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bind_request = "req_bind_42"
        mf = manifest(self.bind_request)
        payload = build_operation_payload("experiment.bind", {"manifest": mf})
        bind = plan_domain_mutation(
            repo_dir=self.root,
            payload=payload,
            request_id=self.bind_request,
            payload_digest=digest_object(payload),
            repository_full_name="owner/repo",
            trusted_binding=TrustedBindingContext(
                host="github.com",
                repository_id="1384446218",
                issue_id="5000000042",
                issue_number="42",
                parent_sha="a" * 40,
            ),
        )
        for path, value in bind.writes.items():
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(value), encoding="utf-8")
        op = {
            "domain_experiment_id": "EXP-42",
            "domain_paths": bind.paths,
            "domain_status": "APPLIED",
            "payload": payload,
            "payload_digest": digest_object(payload),
            "request_id": self.bind_request,
        }
        op_path = self.root / f"operations/{self.bind_request}.json"
        op_path.parent.mkdir(parents=True, exist_ok=True)
        op_path.write_text(json.dumps(op), encoding="utf-8")

    def decision(
        self,
        request_id,
        to_state,
        previous=None,
        reason="human review",
        actor_claim="reviewer",
    ):
        payload = build_operation_payload(
            "experiment.decision",
            {
                "experiment_id": "EXP-42",
                "to_state": to_state,
                "previous_decision_id": previous,
                "reason": reason,
            },
            actor_claim=actor_claim,
        )
        return payload, plan_domain_mutation(
            repo_dir=self.root,
            payload=payload,
            request_id=request_id,
            payload_digest=digest_object(payload),
            repository_full_name="owner/repo",
            trusted_binding=None,
            trusted_actor=TrustedActorContext(
                login="reviewer",
                user_id="101",
                permission="write",
            ),
        )

    def apply(self, plan):
        for path, value in plan.writes.items():
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(value), encoding="utf-8")

    def test_active_to_review_updates_state_and_appends_decision(self):
        payload, plan = self.decision("req_decision_1", "REVIEW")
        self.assertEqual(plan.status, "APPLIED")
        state = plan.writes["experiments/EXP-42/state.json"]
        event = plan.writes["experiments/EXP-42/decisions/req_decision_1.json"]
        self.assertEqual(state["lifecycle"], "REVIEW")
        self.assertEqual(state["sequence"], 1)
        self.assertEqual(state["last_decision_id"], "req_decision_1")
        self.assertEqual(event["from_state"], "ACTIVE")
        self.assertEqual(event["to_state"], "REVIEW")
        self.assertEqual(event["previous_decision_id"], None)
        self.assertEqual(event["actor_claim"], "reviewer")
        self.assertEqual(
            event["actor"],
            {
                "login": "reviewer",
                "user_id": "101",
                "permission": "write",
                "source": "github-collaborator-permission",
            },
        )
        self.assertEqual(digest_object(payload), digest_object(payload))

    def test_decision_requires_trusted_actor(self):
        payload = build_operation_payload(
            "experiment.decision",
            {
                "experiment_id": "EXP-42",
                "to_state": "REVIEW",
                "previous_decision_id": None,
                "reason": "human review",
            },
            actor_claim="reviewer",
        )
        with self.assertRaises(DomainError) as ctx:
            plan_domain_mutation(
                repo_dir=self.root,
                payload=payload,
                request_id="req_no_actor",
                payload_digest=digest_object(payload),
                repository_full_name="owner/repo",
                trusted_binding=None,
                trusted_actor=None,
            )
        self.assertEqual(ctx.exception.code, "DOMAIN_AUTHORIZATION_FAILED")

    def test_read_only_actor_cannot_decide(self):
        payload = build_operation_payload(
            "experiment.decision",
            {
                "experiment_id": "EXP-42",
                "to_state": "REVIEW",
                "previous_decision_id": None,
                "reason": "human review",
            },
            actor_claim="reader",
        )
        with self.assertRaises(DomainError) as ctx:
            plan_domain_mutation(
                repo_dir=self.root,
                payload=payload,
                request_id="req_read_actor",
                payload_digest=digest_object(payload),
                repository_full_name="owner/repo",
                trusted_binding=None,
                trusted_actor=TrustedActorContext(
                    login="reader",
                    user_id="202",
                    permission="read",
                ),
            )
        self.assertEqual(ctx.exception.code, "DOMAIN_AUTHORIZATION_FAILED")

    def test_stale_previous_decision_id_conflicts(self):
        _, first = self.decision("req_decision_1", "REVIEW")
        self.apply(first)
        with self.assertRaises(DomainError) as ctx:
            self.decision("req_decision_2", "ACTIVE", previous=None)
        self.assertEqual(ctx.exception.code, "DOMAIN_DECISION_CONFLICT")

    def test_valid_review_to_active_uses_previous_decision_id(self):
        _, first = self.decision("req_decision_1", "REVIEW")
        self.apply(first)
        _, second = self.decision(
            "req_decision_2",
            "ACTIVE",
            previous="req_decision_1",
        )
        self.assertEqual(second.writes["experiments/EXP-42/state.json"]["lifecycle"], "ACTIVE")
        self.assertEqual(second.writes["experiments/EXP-42/state.json"]["sequence"], 2)

    def test_archive_lock_blocks_decision(self):
        state_path = self.root / "experiments/EXP-42/state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["archive_lock"] = {"archive_id": "archive_1"}
        state_path.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaises(DomainError) as ctx:
            self.decision("req_decision_1", "REVIEW")
        self.assertEqual(ctx.exception.code, "DOMAIN_DECISION_CONFLICT")

    def test_promising_requires_candidate(self):
        _, first = self.decision("req_decision_1", "REVIEW")
        self.apply(first)
        with self.assertRaises(DomainError) as ctx:
            self.decision(
                "req_decision_2",
                "PROMISING",
                previous="req_decision_1",
            )
        self.assertEqual(ctx.exception.code, "DOMAIN_PREREQUISITE_MISSING")

    def test_invalid_transition_rejected(self):
        with self.assertRaises(DomainError) as ctx:
            self.decision("req_decision_1", "REJECTED")
        self.assertEqual(ctx.exception.code, "DOMAIN_INVALID_TRANSITION")

    def test_integrated_and_archived_are_reserved(self):
        for target in ("INTEGRATED", "ARCHIVED"):
            with self.subTest(target=target):
                with self.assertRaises(DomainError) as ctx:
                    self.decision("req_" + target.lower(), target)
                self.assertEqual(ctx.exception.code, "DOMAIN_INVALID_TRANSITION")

    def test_corrupt_binding_operation_blocks_future_decisions(self):
        op_path = self.root / f"operations/{self.bind_request}.json"
        op = json.loads(op_path.read_text(encoding="utf-8"))
        op["payload"]["input"]["manifest"]["scope"]["allowed"].append(".github/**")
        op_path.write_text(json.dumps(op), encoding="utf-8")
        with self.assertRaises(DomainError) as ctx:
            self.decision("req_decision_1", "REVIEW")
        self.assertEqual(ctx.exception.code, "DOMAIN_BOUND_EXPERIMENT_INVALID")


if __name__ == "__main__":
    unittest.main()
