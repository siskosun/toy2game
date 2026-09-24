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
    TrustedCandidateContext,
    TrustedRetentionContext,
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
        "title": "Promotion test",
        "operation_id": request_id,
        "parent": {"experiment": None, "commit": "a" * 40},
        "hypothesis": "Promotion requires current trusted evidence.",
        "success_criteria": ["Only current retained PASS-reviewed Candidate promotes."],
        "kill_criteria": ["Stale or mutable evidence can promote."],
        "scope": {"allowed": ["games/**"], "avoid": [".github/**"]},
        "runtime": {
            "godot": "n/a",
            "export_templates": "n/a",
            "addons_lock": "sha256:none",
        },
        "review": {"protocol": "blind-playtest-v1"},
        "created_at": "2026-09-24T15:00:00+08:00",
    }


class PromotionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.actor = TrustedActorContext(
            login="reviewer",
            user_id="101",
            permission="write",
        )
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
        self.apply(bind)
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
        self.manifest_digest = bind.writes["experiments/EXP-42/binding.json"]["initialization"]["manifest_digest"]

        decision = build_operation_payload(
            "experiment.decision",
            {
                "experiment_id": "EXP-42",
                "to_state": "REVIEW",
                "previous_decision_id": None,
                "reason": "start human review",
            },
            actor_claim="reviewer",
        )
        review_state = plan_domain_mutation(
            repo_dir=self.root,
            payload=decision,
            request_id="req_decision_review",
            payload_digest=digest_object(decision),
            repository_full_name="owner/repo",
            trusted_actor=self.actor,
        )
        self.apply(review_state)

        self.artifact_digest = "sha256:" + "c" * 64
        self.source_sha = "b" * 40
        self.candidate_id = "C-42-123-1"
        candidate_payload = build_operation_payload(
            "candidate.register",
            {"experiment_id": "EXP-42"},
        )
        candidate = plan_domain_mutation(
            repo_dir=self.root,
            payload=candidate_payload,
            request_id="req_candidate",
            payload_digest=digest_object(candidate_payload),
            repository_full_name="owner/repo",
            trusted_candidate=TrustedCandidateContext(
                experiment_id="EXP-42",
                candidate_id=self.candidate_id,
                source_sha=self.source_sha,
                manifest_digest=self.manifest_digest,
                artifact_digest=self.artifact_digest,
                policy_digest="sha256:" + "d" * 64,
                workflow_source_sha="e" * 40,
                run_id="123",
                run_attempt="1",
                checks=(
                    {"name": "build", "status": "PASS", "source": "TRUSTED_OBSERVED"},
                ),
                retention={
                    "provider": "github-immutable-release",
                    "release_tag": "game-exp-candidate-42-123-1",
                    "release_url": "https://github.com/owner/repo/releases/tag/game-exp-candidate-42-123-1",
                    "immutable": True,
                    "artifact_digest": self.artifact_digest,
                },
                attestation={
                    "provider": "github-artifact-attestations",
                    "verified": True,
                    "subject_digest": self.artifact_digest,
                    "source_sha": self.source_sha,
                },
            ),
        )
        self.apply(candidate)

    def apply(self, plan):
        for path, value in plan.writes.items():
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(value), encoding="utf-8")

    def record_review(self, outcome="PASS"):
        payload = build_operation_payload(
            "review.record",
            {
                "experiment_id": "EXP-42",
                "candidate_id": self.candidate_id,
                "outcome": outcome,
                "notes": "human playtest",
            },
            actor_claim="reviewer",
        )
        plan = plan_domain_mutation(
            repo_dir=self.root,
            payload=payload,
            request_id="req_review",
            payload_digest=digest_object(payload),
            repository_full_name="owner/repo",
            trusted_actor=self.actor,
        )
        self.apply(plan)

    def retention(self, **overrides):
        values = {
            "experiment_id": "EXP-42",
            "candidate_id": self.candidate_id,
            "release_id": "9001",
            "release_tag": "game-exp-candidate-42-123-1",
            "release_url": "https://github.com/owner/repo/releases/tag/game-exp-candidate-42-123-1",
            "immutable": True,
            "target_commitish": self.source_sha,
            "artifact_name": "candidate.tgz",
            "artifact_digest": self.artifact_digest,
            "asset_id": "8001",
        }
        values.update(overrides)
        return TrustedRetentionContext(**values)

    def promote(self, trusted_retention):
        payload = build_operation_payload(
            "experiment.decision",
            {
                "experiment_id": "EXP-42",
                "to_state": "PROMISING",
                "previous_decision_id": "req_decision_review",
                "reason": "candidate passed retained human review",
            },
            actor_claim="reviewer",
        )
        return plan_domain_mutation(
            repo_dir=self.root,
            payload=payload,
            request_id="req_promising",
            payload_digest=digest_object(payload),
            repository_full_name="owner/repo",
            trusted_actor=self.actor,
            trusted_retention=trusted_retention,
        )

    def test_promising_binds_candidate_review_and_runtime_retention(self):
        self.record_review("PASS")
        plan = self.promote(self.retention())
        state = plan.writes["experiments/EXP-42/state.json"]
        decision = plan.writes["experiments/EXP-42/decisions/req_promising.json"]
        self.assertEqual(state["lifecycle"], "PROMISING")
        evidence = decision["promotion_evidence"]
        self.assertEqual(evidence["candidate_id"], self.candidate_id)
        self.assertEqual(evidence["review_id"], "req_review")
        self.assertEqual(evidence["artifact_digest"], self.artifact_digest)
        self.assertEqual(evidence["retention"]["release_id"], "9001")
        self.assertTrue(evidence["retention"]["immutable"])

    def test_promising_requires_current_pass_review(self):
        self.record_review("FAIL")
        with self.assertRaises(DomainError) as ctx:
            self.promote(self.retention())
        self.assertEqual(ctx.exception.code, "DOMAIN_PREREQUISITE_MISSING")

    def test_promising_requires_runtime_retention_verification(self):
        self.record_review("PASS")
        with self.assertRaises(DomainError) as ctx:
            self.promote(None)
        self.assertEqual(ctx.exception.code, "DOMAIN_PREREQUISITE_MISSING")

    def test_promising_rejects_retention_digest_mismatch(self):
        self.record_review("PASS")
        with self.assertRaises(DomainError) as ctx:
            self.promote(self.retention(artifact_digest="sha256:" + "f" * 64))
        self.assertEqual(ctx.exception.code, "DOMAIN_PREREQUISITE_MISSING")

    def test_promising_rejects_retention_source_mismatch(self):
        self.record_review("PASS")
        with self.assertRaises(DomainError) as ctx:
            self.promote(self.retention(target_commitish="f" * 40))
        self.assertEqual(ctx.exception.code, "DOMAIN_PREREQUISITE_MISSING")

    def test_selected_remains_blocked_until_rehearsal(self):
        self.record_review("PASS")
        promising = self.promote(self.retention())
        self.apply(promising)
        payload = build_operation_payload(
            "experiment.decision",
            {
                "experiment_id": "EXP-42",
                "to_state": "SELECTED",
                "previous_decision_id": "req_promising",
                "reason": "select",
            },
            actor_claim="reviewer",
        )
        with self.assertRaises(DomainError) as ctx:
            plan_domain_mutation(
                repo_dir=self.root,
                payload=payload,
                request_id="req_selected",
                payload_digest=digest_object(payload),
                repository_full_name="owner/repo",
                trusted_actor=self.actor,
            )
        self.assertEqual(ctx.exception.code, "DOMAIN_PREREQUISITE_MISSING")


if __name__ == "__main__":
    unittest.main()
