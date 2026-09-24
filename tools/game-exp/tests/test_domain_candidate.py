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
    TrustedBindingContext,
    TrustedCandidateContext,
    candidate_policy_digest,
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
        "title": "Candidate test",
        "operation_id": request_id,
        "parent": {"experiment": None, "commit": "a" * 40},
        "hypothesis": "Trusted Candidate evidence gates promotion.",
        "success_criteria": ["Only independently verified Level-3 Candidates promote."],
        "kill_criteria": ["Project-reported evidence can satisfy promotion."],
        "scope": {"allowed": ["games/**"], "avoid": [".github/**"]},
        "runtime": {
            "godot": "n/a",
            "export_templates": "n/a",
            "addons_lock": "sha256:none",
        },
        "review": {"protocol": "blind-playtest-v1"},
        "created_at": "2026-09-24T14:10:00+08:00",
    }


def checks():
    return [
        {"name": "project-tests", "provenance": "TRUSTED_OBSERVED", "status": "PASS"},
        {"name": "project-build", "provenance": "TRUSTED_OBSERVED", "status": "PASS"},
        {"name": "artifact-observe", "provenance": "TRUSTED_OBSERVED", "status": "PASS"},
        {"name": "attestation-verify", "provenance": "TRUSTED_OBSERVED", "status": "PASS"},
        {"name": "immutable-retention", "provenance": "TRUSTED_OBSERVED", "status": "PASS"},
    ]


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bind_request = "req_bind_42"
        self.mf = manifest(self.bind_request)
        bind_payload = build_operation_payload("experiment.bind", {"manifest": self.mf})
        bind_plan = plan_domain_mutation(
            repo_dir=self.root,
            payload=bind_payload,
            request_id=self.bind_request,
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
        self._apply(bind_plan)
        op = {
            "domain_experiment_id": "EXP-42",
            "domain_paths": bind_plan.paths,
            "domain_status": "APPLIED",
            "payload": bind_payload,
            "payload_digest": digest_object(bind_payload),
            "request_id": self.bind_request,
        }
        self._write(f"operations/{self.bind_request}.json", op)

        review_payload = build_operation_payload(
            "experiment.decision",
            {
                "experiment_id": "EXP-42",
                "to_state": "REVIEW",
                "previous_decision_id": None,
                "reason": "enter review",
            },
        )
        review_plan = plan_domain_mutation(
            repo_dir=self.root,
            payload=review_payload,
            request_id="req_decision_review",
            payload_digest=digest_object(review_payload),
            repository_full_name="owner/repo",
        )
        self._apply(review_plan)

        self.source_sha = "c" * 40
        self.workflow_sha = "b" * 40
        self.artifact_digest = "sha256:" + "1" * 64
        self.manifest_digest = digest_object(self.mf)
        self.dependency_lock_digest = "sha256:" + "4" * 64
        self.environment_digest = "sha256:" + "5" * 64
        self.candidate_id = "C-42-123-1"
        self.anchor = f"refs/tags/exp-candidate/42/{self.candidate_id}"
        self.release_tag = "game-exp-candidate-123-1"

    def _write(self, path, value):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value), encoding="utf-8")

    def _apply(self, plan):
        for path, value in plan.writes.items():
            self._write(path, value)

    def _input(self):
        return {
            "experiment_id": "EXP-42",
            "candidate_id": self.candidate_id,
            "source_sha": self.source_sha,
            "source_anchor_ref": self.anchor,
            "manifest_digest": self.manifest_digest,
            "artifact_digest": self.artifact_digest,
            "artifact_level": 3,
            "workflow_source_sha": self.workflow_sha,
            "run_id": "123",
            "run_attempt": "1",
            "policy_digest": candidate_policy_digest(),
            "dependency_lock_digest": self.dependency_lock_digest,
            "environment_digest": self.environment_digest,
            "checks": checks(),
            "retention": {
                "provider": "github-immutable-release",
                "release_tag": self.release_tag,
                "asset_name": "candidate.tgz",
            },
        }

    def _context(self, **overrides):
        values = {
            "experiment_id": "EXP-42",
            "candidate_id": self.candidate_id,
            "source_anchor_ref": self.anchor,
            "source_sha": self.source_sha,
            "artifact_digest": self.artifact_digest,
            "manifest_digest": self.manifest_digest,
            "workflow_source_sha": self.workflow_sha,
            "run_id": "123",
            "run_attempt": "1",
            "policy_digest": candidate_policy_digest(),
            "dependency_lock_digest": self.dependency_lock_digest,
            "environment_digest": self.environment_digest,
            "release_tag": self.release_tag,
        }
        values.update(overrides)
        return TrustedCandidateContext(**values)

    def attest(self, request_id="req_candidate_123_1", input_value=None, context=None):
        input_value = input_value or self._input()
        payload = build_operation_payload(
            "candidate.attest",
            input_value,
            actor_claim="trusted-candidate-workflow",
        )
        return payload, plan_domain_mutation(
            repo_dir=self.root,
            payload=payload,
            request_id=request_id,
            payload_digest=digest_object(payload),
            repository_full_name="owner/repo",
            trusted_candidate=context or self._context(),
        )

    def test_candidate_attest_writes_record_and_pointer(self):
        payload, plan = self.attest()
        self.assertEqual(plan.status, "APPLIED")
        candidate_path = f"experiments/EXP-42/candidates/{self.candidate_id}.json"
        pointer_path = "experiments/EXP-42/current_candidate.json"
        record = plan.writes[candidate_path]
        pointer = plan.writes[pointer_path]
        self.assertEqual(record["artifact_level"], 3)
        self.assertEqual(record["checks"], checks())
        self.assertEqual(record["dependency_lock_digest"], self.dependency_lock_digest)
        self.assertEqual(record["environment_digest"], self.environment_digest)
        self.assertEqual(record["runtime"], self.mf["runtime"])
        self.assertEqual(pointer["candidate_id"], self.candidate_id)
        self.assertEqual(pointer["candidate_digest"], digest_object(record))
        self.assertEqual(digest_object(payload), digest_object(payload))

    def test_valid_current_candidate_allows_review_to_promising(self):
        _, candidate_plan = self.attest()
        self._apply(candidate_plan)
        payload = build_operation_payload(
            "experiment.decision",
            {
                "experiment_id": "EXP-42",
                "to_state": "PROMISING",
                "previous_decision_id": "req_decision_review",
                "reason": "candidate passed trusted gates",
            },
        )
        decision = plan_domain_mutation(
            repo_dir=self.root,
            payload=payload,
            request_id="req_decision_promising",
            payload_digest=digest_object(payload),
            repository_full_name="owner/repo",
        )
        state = decision.writes["experiments/EXP-42/state.json"]
        self.assertEqual(state["lifecycle"], "PROMISING")

    def test_project_reported_check_cannot_satisfy_policy(self):
        value = self._input()
        value["checks"][0]["provenance"] = "PROJECT_REPORTED"
        with self.assertRaisesRegex(DomainError, "not trusted PASS"):
            self.attest(input_value=value)

    def test_failed_check_cannot_satisfy_policy(self):
        value = self._input()
        value["checks"][1]["status"] = "FAIL"
        with self.assertRaisesRegex(DomainError, "not trusted PASS"):
            self.attest(input_value=value)

    def test_level_two_candidate_rejected(self):
        value = self._input()
        value["artifact_level"] = 2
        with self.assertRaisesRegex(DomainError, "artifact_level"):
            self.attest(input_value=value)

    def test_stale_policy_digest_rejected(self):
        value = self._input()
        value["policy_digest"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(DomainError, "policy digest"):
            self.attest(input_value=value)

    def test_independent_context_mismatch_conflicts(self):
        with self.assertRaises(DomainError) as ctx:
            self.attest(context=self._context(source_sha="d" * 40))
        self.assertEqual(ctx.exception.code, "DOMAIN_CANDIDATE_CONFLICT")

    def test_candidate_id_must_bind_run_identity(self):
        value = self._input()
        value["run_attempt"] = "2"
        with self.assertRaisesRegex(DomainError, "run identity"):
            self.attest(input_value=value)

    def test_candidate_requires_review_or_promising(self):
        state_path = self.root / "experiments/EXP-42/state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["lifecycle"] = "ACTIVE"
        state["last_decision_id"] = None
        state["sequence"] = 0
        state_path.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaises(DomainError) as ctx:
            self.attest()
        self.assertEqual(ctx.exception.code, "DOMAIN_INVALID_TRANSITION")

    def test_corrupt_candidate_pointer_blocks_promotion(self):
        _, candidate_plan = self.attest()
        self._apply(candidate_plan)
        pointer_path = self.root / "experiments/EXP-42/current_candidate.json"
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        pointer["candidate_digest"] = "sha256:" + "0" * 64
        pointer_path.write_text(json.dumps(pointer), encoding="utf-8")
        payload = build_operation_payload(
            "experiment.decision",
            {
                "experiment_id": "EXP-42",
                "to_state": "PROMISING",
                "previous_decision_id": "req_decision_review",
                "reason": "should fail",
            },
        )
        with self.assertRaises(DomainError) as ctx:
            plan_domain_mutation(
                repo_dir=self.root,
                payload=payload,
                request_id="req_decision_promising",
                payload_digest=digest_object(payload),
                repository_full_name="owner/repo",
            )
        self.assertEqual(ctx.exception.code, "DOMAIN_PREREQUISITE_MISSING")


if __name__ == "__main__":
    unittest.main()
