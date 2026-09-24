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
        "hypothesis": "Trusted Candidate receipts bind the reviewed bytes.",
        "success_criteria": ["Only trusted observed builds become Candidates."],
        "kill_criteria": ["Project code can forge a Candidate receipt."],
        "scope": {"allowed": ["games/**"], "avoid": [".github/**"]},
        "runtime": {
            "godot": "n/a",
            "export_templates": "n/a",
            "addons_lock": "sha256:none",
        },
        "review": {"protocol": "blind-playtest-v1"},
        "created_at": "2026-09-24T14:30:00+08:00",
    }


def candidate_context(**overrides):
    base = {
        "experiment_id": "EXP-42",
        "candidate_id": "C-42-123456-1",
        "source_sha": "b" * 40,
        "manifest_digest": None,
        "artifact_digest": "sha256:" + "c" * 64,
        "policy_digest": "sha256:" + "d" * 64,
        "workflow_source_sha": "e" * 40,
        "run_id": "123456",
        "run_attempt": "1",
        "checks": (
            {"name": "project_tests", "status": "PASS", "source": "TRUSTED_OBSERVED"},
            {"name": "build", "status": "PASS", "source": "TRUSTED_OBSERVED"},
            {"name": "artifact_structure", "status": "PASS", "source": "TRUSTED_OBSERVED"},
        ),
        "retention": {
            "provider": "github-immutable-release",
            "release_tag": "game-exp-candidate-42-123456-1",
            "release_url": "https://github.com/owner/repo/releases/tag/game-exp-candidate-42-123456-1",
            "immutable": True,
            "artifact_digest": "sha256:" + "c" * 64,
        },
        "attestation": {
            "provider": "github-artifact-attestations",
            "verified": True,
            "subject_digest": "sha256:" + "c" * 64,
            "source_sha": "b" * 40,
        },
    }
    base.update(overrides)
    return base


class CandidateTests(unittest.TestCase):
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
        self.manifest_digest = bind.writes["experiments/EXP-42/binding.json"]["initialization"]["manifest_digest"]

    def context(self, **overrides):
        value = candidate_context(manifest_digest=self.manifest_digest)
        value.update(overrides)
        return TrustedCandidateContext(**value)

    def plan(self, context=None):
        payload = build_operation_payload(
            "candidate.register",
            {"experiment_id": "EXP-42"},
        )
        return plan_domain_mutation(
            repo_dir=self.root,
            payload=payload,
            request_id="req_candidate_1",
            payload_digest=digest_object(payload),
            repository_full_name="owner/repo",
            trusted_candidate=context if context is not None else self.context(),
        )

    def test_trusted_candidate_updates_current_candidate(self):
        plan = self.plan()
        self.assertEqual(plan.status, "APPLIED")
        path = "experiments/EXP-42/candidates/C-42-123456-1.json"
        candidate = plan.writes[path]
        state = plan.writes["experiments/EXP-42/state.json"]
        self.assertEqual(candidate["source_sha"], "b" * 40)
        self.assertEqual(candidate["artifact_digest"], "sha256:" + "c" * 64)
        self.assertEqual(candidate["github_run_id"], "123456")
        self.assertEqual(state["current_candidate_id"], "C-42-123456-1")
        self.assertEqual(state["candidate_sequence"], 1)
        self.assertEqual(state["lifecycle"], "ACTIVE")

    def test_candidate_requires_trusted_context(self):
        payload = build_operation_payload(
            "candidate.register",
            {"experiment_id": "EXP-42"},
        )
        with self.assertRaises(DomainError) as ctx:
            plan_domain_mutation(
                repo_dir=self.root,
                payload=payload,
                request_id="req_candidate_missing",
                payload_digest=digest_object(payload),
                repository_full_name="owner/repo",
                trusted_candidate=None,
            )
        self.assertEqual(ctx.exception.code, "DOMAIN_AUTHORIZATION_FAILED")

    def test_manifest_digest_mismatch_rejected(self):
        with self.assertRaises(DomainError) as ctx:
            self.plan(self.context(manifest_digest="sha256:" + "f" * 64))
        self.assertEqual(ctx.exception.code, "DOMAIN_CANDIDATE_CONFLICT")

    def test_untrusted_check_rejected(self):
        checks = (
            {"name": "project_tests", "status": "PASS", "source": "PROJECT_REPORTED"},
        )
        with self.assertRaises(DomainError) as ctx:
            self.plan(self.context(checks=checks))
        self.assertEqual(ctx.exception.code, "DOMAIN_PREREQUISITE_MISSING")

    def test_failed_check_rejected(self):
        checks = (
            {"name": "project_tests", "status": "FAIL", "source": "TRUSTED_OBSERVED"},
        )
        with self.assertRaises(DomainError) as ctx:
            self.plan(self.context(checks=checks))
        self.assertEqual(ctx.exception.code, "DOMAIN_PREREQUISITE_MISSING")

    def test_nonimmutable_retention_rejected(self):
        retention = dict(candidate_context()["retention"])
        retention["immutable"] = False
        with self.assertRaises(DomainError) as ctx:
            self.plan(self.context(retention=retention))
        self.assertEqual(ctx.exception.code, "DOMAIN_PREREQUISITE_MISSING")

    def test_attestation_source_mismatch_rejected(self):
        attestation = dict(candidate_context()["attestation"])
        attestation["source_sha"] = "f" * 40
        with self.assertRaises(DomainError) as ctx:
            self.plan(self.context(attestation=attestation))
        self.assertEqual(ctx.exception.code, "DOMAIN_CANDIDATE_CONFLICT")

    def test_candidate_id_must_match_experiment_and_run(self):
        with self.assertRaises(DomainError) as ctx:
            self.plan(self.context(candidate_id="C-41-123456-1"))
        self.assertEqual(ctx.exception.code, "DOMAIN_CANDIDATE_CONFLICT")

    def test_candidate_registration_rejected_after_rejection(self):
        state_path = self.root / "experiments/EXP-42/state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["lifecycle"] = "REJECTED"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaises(DomainError) as ctx:
            self.plan()
        self.assertEqual(ctx.exception.code, "DOMAIN_CANDIDATE_CONFLICT")

    def test_duplicate_candidate_id_conflicts(self):
        first = self.plan()
        for path, value in first.writes.items():
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(DomainError) as ctx:
            self.plan()
        self.assertEqual(ctx.exception.code, "DOMAIN_CANDIDATE_CONFLICT")


if __name__ == "__main__":
    unittest.main()
