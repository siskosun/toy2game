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
        "title": "Review test",
        "operation_id": request_id,
        "parent": {"experiment": None, "commit": "a" * 40},
        "hypothesis": "Human Review binds exact Candidate bytes.",
        "success_criteria": ["Review cannot drift to a different Candidate."],
        "kill_criteria": ["A stale or untrusted Review can authorize promotion."],
        "scope": {"allowed": ["games/**"], "avoid": [".github/**"]},
        "runtime": {
            "godot": "n/a",
            "export_templates": "n/a",
            "addons_lock": "sha256:none",
        },
        "review": {"protocol": "blind-playtest-v1"},
        "created_at": "2026-09-24T15:00:00+08:00",
    }


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.actor = TrustedActorContext(
            login="reviewer",
            user_id="101",
            permission="write",
        )

        bind_request = "req_bind_42"
        mf = manifest(bind_request)
        bind_payload = build_operation_payload("experiment.bind", {"manifest": mf})
        bind = plan_domain_mutation(
            repo_dir=self.root,
            payload=bind_payload,
            request_id=bind_request,
            payload_digest=digest_object(bind_payload),
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
            "payload": bind_payload,
            "payload_digest": digest_object(bind_payload),
            "request_id": bind_request,
        }
        op_path = self.root / f"operations/{bind_request}.json"
        op_path.parent.mkdir(parents=True, exist_ok=True)
        op_path.write_text(json.dumps(op), encoding="utf-8")

        binding = bind.writes["experiments/EXP-42/binding.json"]
        self.manifest_digest = binding["initialization"]["manifest_digest"]
        candidate = self.candidate_context()
        candidate_payload = build_operation_payload(
            "candidate.register",
            {"experiment_id": "EXP-42"},
        )
        candidate_plan = plan_domain_mutation(
            repo_dir=self.root,
            payload=candidate_payload,
            request_id="req_candidate_1",
            payload_digest=digest_object(candidate_payload),
            repository_full_name="owner/repo",
            trusted_candidate=candidate,
        )
        self.apply(candidate_plan)

        decision_payload = build_operation_payload(
            "experiment.decision",
            {
                "experiment_id": "EXP-42",
                "to_state": "REVIEW",
                "previous_decision_id": None,
                "reason": "ready for playtest",
            },
            actor_claim="reviewer",
        )
        decision = plan_domain_mutation(
            repo_dir=self.root,
            payload=decision_payload,
            request_id="req_decision_review",
            payload_digest=digest_object(decision_payload),
            repository_full_name="owner/repo",
            trusted_actor=self.actor,
        )
        self.apply(decision)

    def apply(self, plan):
        for path, value in plan.writes.items():
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(value), encoding="utf-8")

    def candidate_context(self, *, run_id="123456", attempt="1"):
        candidate_id = f"C-42-{run_id}-{attempt}"
        artifact_digest = "sha256:" + "c" * 64
        return TrustedCandidateContext(
            experiment_id="EXP-42",
            candidate_id=candidate_id,
            source_sha="b" * 40,
            manifest_digest=self.manifest_digest,
            artifact_digest=artifact_digest,
            policy_digest="sha256:" + "d" * 64,
            workflow_source_sha="e" * 40,
            run_id=run_id,
            run_attempt=attempt,
            checks=(
                {"name": "project_tests", "status": "PASS", "source": "TRUSTED_OBSERVED"},
                {"name": "build", "status": "PASS", "source": "TRUSTED_OBSERVED"},
                {"name": "artifact_structure", "status": "PASS", "source": "TRUSTED_OBSERVED"},
            ),
            retention={
                "provider": "github-immutable-release",
                "release_tag": f"game-exp-candidate-42-{run_id}-{attempt}",
                "release_url": f"https://github.com/owner/repo/releases/tag/game-exp-candidate-42-{run_id}-{attempt}",
                "immutable": True,
                "artifact_digest": artifact_digest,
            },
            attestation={
                "provider": "github-artifact-attestations",
                "verified": True,
                "subject_digest": artifact_digest,
                "source_sha": "b" * 40,
            },
        )

    def review(
        self,
        *,
        request_id="req_review_1",
        candidate_id="C-42-123456-1",
        outcome="PASS",
        notes="The tested Candidate meets the review protocol.",
        actor=None,
    ):
        payload = build_operation_payload(
            "review.record",
            {
                "experiment_id": "EXP-42",
                "candidate_id": candidate_id,
                "outcome": outcome,
                "notes": notes,
            },
            actor_claim="human-reviewer",
        )
        return payload, plan_domain_mutation(
            repo_dir=self.root,
            payload=payload,
            request_id=request_id,
            payload_digest=digest_object(payload),
            repository_full_name="owner/repo",
            trusted_actor=self.actor if actor is None else actor,
        )

    def test_review_binds_current_candidate_artifact_and_actor(self):
        _payload, plan = self.review()
        review = plan.writes["experiments/EXP-42/reviews/req_review_1.json"]
        state = plan.writes["experiments/EXP-42/state.json"]
        self.assertEqual(review["candidate_id"], "C-42-123456-1")
        self.assertEqual(review["artifact_digest"], "sha256:" + "c" * 64)
        self.assertEqual(review["protocol"], "blind-playtest-v1")
        self.assertEqual(review["outcome"], "PASS")
        self.assertEqual(review["actor"]["login"], "reviewer")
        self.assertEqual(review["actor"]["user_id"], "101")
        self.assertEqual(review["actor"]["permission"], "write")
        self.assertEqual(review["actor_claim"], "human-reviewer")
        self.assertEqual(state["lifecycle"], "REVIEW")
        self.assertEqual(state["current_review_id"], "req_review_1")
        self.assertEqual(state["current_review_candidate_id"], "C-42-123456-1")
        self.assertEqual(state["review_sequence"], 1)

    def test_review_requires_trusted_actor(self):
        payload = build_operation_payload(
            "review.record",
            {
                "experiment_id": "EXP-42",
                "candidate_id": "C-42-123456-1",
                "outcome": "PASS",
                "notes": "review",
            },
        )
        with self.assertRaises(DomainError) as ctx:
            plan_domain_mutation(
                repo_dir=self.root,
                payload=payload,
                request_id="req_review_no_actor",
                payload_digest=digest_object(payload),
                repository_full_name="owner/repo",
                trusted_actor=None,
            )
        self.assertEqual(ctx.exception.code, "DOMAIN_AUTHORIZATION_FAILED")

    def test_read_only_actor_cannot_review(self):
        with self.assertRaises(DomainError) as ctx:
            self.review(
                actor=TrustedActorContext(
                    login="reader",
                    user_id="202",
                    permission="read",
                )
            )
        self.assertEqual(ctx.exception.code, "DOMAIN_AUTHORIZATION_FAILED")

    def test_review_requires_review_lifecycle(self):
        state_path = self.root / "experiments/EXP-42/state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["lifecycle"] = "ACTIVE"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaises(DomainError) as ctx:
            self.review()
        self.assertEqual(ctx.exception.code, "DOMAIN_REVIEW_CONFLICT")

    def test_review_must_bind_current_candidate(self):
        with self.assertRaises(DomainError) as ctx:
            self.review(candidate_id="C-42-999999-1")
        self.assertEqual(ctx.exception.code, "DOMAIN_REVIEW_CONFLICT")

    def test_invalid_outcome_rejected(self):
        with self.assertRaisesRegex(DomainError, "PASS or FAIL"):
            self.review(outcome="MAYBE")

    def test_corrupt_candidate_retention_blocks_review(self):
        path = self.root / "experiments/EXP-42/candidates/C-42-123456-1.json"
        candidate = json.loads(path.read_text(encoding="utf-8"))
        candidate["retention"]["immutable"] = False
        path.write_text(json.dumps(candidate), encoding="utf-8")
        with self.assertRaises(DomainError) as ctx:
            self.review()
        self.assertEqual(ctx.exception.code, "DOMAIN_REVIEW_CONFLICT")

    def test_candidate_manifest_drift_blocks_review(self):
        path = self.root / "experiments/EXP-42/candidates/C-42-123456-1.json"
        candidate = json.loads(path.read_text(encoding="utf-8"))
        candidate["manifest_digest"] = "sha256:" + "f" * 64
        path.write_text(json.dumps(candidate), encoding="utf-8")
        with self.assertRaises(DomainError) as ctx:
            self.review()
        self.assertEqual(ctx.exception.code, "DOMAIN_REVIEW_CONFLICT")

    def test_duplicate_review_id_conflicts(self):
        _payload, first = self.review()
        self.apply(first)
        with self.assertRaises(DomainError) as ctx:
            self.review()
        self.assertEqual(ctx.exception.code, "DOMAIN_REVIEW_CONFLICT")

    def test_new_candidate_invalidates_current_review(self):
        _payload, review = self.review()
        self.apply(review)
        self.assertIn(
            "current_review_id",
            json.loads(
                (self.root / "experiments/EXP-42/state.json").read_text(encoding="utf-8")
            ),
        )

        second_context = self.candidate_context(run_id="123457")
        payload = build_operation_payload(
            "candidate.register",
            {"experiment_id": "EXP-42"},
        )
        second = plan_domain_mutation(
            repo_dir=self.root,
            payload=payload,
            request_id="req_candidate_2",
            payload_digest=digest_object(payload),
            repository_full_name="owner/repo",
            trusted_candidate=second_context,
        )
        state = second.writes["experiments/EXP-42/state.json"]
        self.assertEqual(state["current_candidate_id"], "C-42-123457-1")
        self.assertNotIn("current_review_id", state)
        self.assertNotIn("current_review_candidate_id", state)


if __name__ == "__main__":
    unittest.main()
