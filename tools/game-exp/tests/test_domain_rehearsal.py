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
    REHEARSAL_REQUIRED_CHECKS,
    TrustedRehearsalContext,
    plan_domain_mutation,
    rehearsal_policy_digest,
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
        "title": "Rehearsal test",
        "operation_id": request_id,
        "parent": {"experiment": None, "commit": "a" * 40},
        "hypothesis": "Latest-main rehearsal is reproducible.",
        "success_criteria": ["Trusted merge tree and project checks pass."],
        "kill_criteria": ["Stale main or wrong Candidate can rehearse."],
        "scope": {"allowed": ["games/**"], "avoid": [".github/**"]},
        "runtime": {
            "godot": "n/a",
            "export_templates": "n/a",
            "addons_lock": "sha256:none",
        },
        "review": {"protocol": "blind-playtest-v1"},
        "created_at": "2026-09-24T15:20:00+08:00",
    }


class RehearsalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        bind_request = "req_bind_42"
        mf = manifest(bind_request)
        payload = build_operation_payload("experiment.bind", {"manifest": mf})
        bind = plan_domain_mutation(
            repo_dir=self.root,
            payload=payload,
            request_id=bind_request,
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
        op_path = self.root / f"operations/{bind_request}.json"
        op_path.parent.mkdir(parents=True, exist_ok=True)
        op_path.write_text(
            json.dumps(
                {
                    "domain_experiment_id": "EXP-42",
                    "domain_paths": bind.paths,
                    "domain_status": "APPLIED",
                    "payload": payload,
                    "payload_digest": digest_object(payload),
                    "request_id": bind_request,
                }
            ),
            encoding="utf-8",
        )

        self.candidate_id = "C-42-123-1"
        self.source_sha = "b" * 40
        candidate_path = (
            self.root
            / f"experiments/EXP-42/candidates/{self.candidate_id}.json"
        )
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        candidate_path.write_text(
            json.dumps(
                {
                    "kind": "candidate",
                    "candidate_id": self.candidate_id,
                    "experiment_id": "EXP-42",
                    "source_sha": self.source_sha,
                    "manifest_digest": bind.writes[
                        "experiments/EXP-42/binding.json"
                    ]["initialization"]["manifest_digest"],
                    "artifact_digest": "sha256:" + "c" * 64,
                    "policy_digest": "sha256:" + "d" * 64,
                    "workflow_source_sha": "e" * 40,
                    "github_run_id": "123",
                    "github_run_attempt": "1",
                    "checks": [
                        {
                            "name": "build",
                            "status": "PASS",
                            "source": "TRUSTED_OBSERVED",
                        }
                    ],
                    "retention": {
                        "provider": "github-immutable-release",
                        "release_tag": "game-exp-candidate-42-123-1",
                        "release_url": "https://github.com/owner/repo/releases/tag/game-exp-candidate-42-123-1",
                        "immutable": True,
                        "artifact_digest": "sha256:" + "c" * 64,
                    },
                    "attestation": {
                        "provider": "github-artifact-attestations",
                        "verified": True,
                        "subject_digest": "sha256:" + "c" * 64,
                        "source_sha": self.source_sha,
                    },
                }
            ),
            encoding="utf-8",
        )
        state_path = self.root / "experiments/EXP-42/state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state.update(
            {
                "lifecycle": "PROMISING",
                "sequence": 2,
                "last_decision_id": "req_promising",
                "current_candidate_id": self.candidate_id,
            }
        )
        state_path.write_text(json.dumps(state), encoding="utf-8")

    def apply(self, plan):
        for path, value in plan.writes.items():
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(value), encoding="utf-8")

    def context(self, **overrides):
        values = {
            "experiment_id": "EXP-42",
            "candidate_id": self.candidate_id,
            "rehearsal_id": "R-42-456-1",
            "main_sha": "1" * 40,
            "source_sha": self.source_sha,
            "integration_sha": "2" * 40,
            "integration_tree_sha": "3" * 40,
            "rehearsal_ref": "refs/tags/exp-rehearsal/42/R-42-456-1",
            "workflow_source_sha": "4" * 40,
            "run_id": "456",
            "run_attempt": "1",
            "policy_digest": rehearsal_policy_digest(),
            "scope_digest": "sha256:" + "6" * 64,
            "checks": tuple(
                {"name": name, "status": "PASS", "source": "TRUSTED_OBSERVED"}
                for name in REHEARSAL_REQUIRED_CHECKS
            ),
        }
        values.update(overrides)
        return TrustedRehearsalContext(**values)

    def plan(self, ctx=None):
        payload = build_operation_payload(
            "rehearsal.register",
            {"experiment_id": "EXP-42"},
            preconditions={
                "candidate_id": self.candidate_id,
                "main_sha": "1" * 40,
            },
        )
        return plan_domain_mutation(
            repo_dir=self.root,
            payload=payload,
            request_id="req_rehearsal_456_1",
            payload_digest=digest_object(payload),
            repository_full_name="owner/repo",
            trusted_rehearsal=ctx if ctx is not None else self.context(),
        )

    def test_rehearsal_binds_candidate_main_integration_tree_and_checks(self):
        plan = self.plan()
        record = plan.writes[
            "experiments/EXP-42/rehearsals/R-42-456-1.json"
        ]
        state = plan.writes["experiments/EXP-42/state.json"]
        self.assertEqual(record["candidate_id"], self.candidate_id)
        self.assertEqual(record["main_sha"], "1" * 40)
        self.assertEqual(record["source_sha"], self.source_sha)
        self.assertEqual(record["integration_sha"], "2" * 40)
        self.assertEqual(record["integration_tree_sha"], "3" * 40)
        self.assertEqual(record["scope_digest"], "sha256:" + "6" * 64)
        self.assertEqual(state["lifecycle"], "PROMISING")
        self.assertEqual(state["current_rehearsal_id"], "R-42-456-1")
        self.assertEqual(state["current_rehearsal_candidate_id"], self.candidate_id)

    def test_rehearsal_requires_promising(self):
        state_path = self.root / "experiments/EXP-42/state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["lifecycle"] = "REVIEW"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaises(DomainError) as ctx:
            self.plan()
        self.assertEqual(ctx.exception.code, "DOMAIN_INVALID_TRANSITION")

    def test_rehearsal_requires_current_candidate_source(self):
        with self.assertRaises(DomainError) as ctx:
            self.plan(self.context(source_sha="f" * 40))
        self.assertEqual(ctx.exception.code, "DOMAIN_REHEARSAL_CONFLICT")

    def test_rehearsal_rejects_untrusted_check(self):
        bad = (
            {"name": "merge", "status": "PASS", "source": "PROJECT_REPORTED"},
        )
        with self.assertRaises(DomainError) as ctx:
            self.plan(self.context(checks=bad))
        self.assertEqual(ctx.exception.code, "DOMAIN_PREREQUISITE_MISSING")

    def test_rehearsal_rejects_policy_digest_drift(self):
        with self.assertRaises(DomainError) as ctx:
            self.plan(self.context(policy_digest="sha256:" + "0" * 64))
        self.assertEqual(ctx.exception.code, "DOMAIN_REHEARSAL_CONFLICT")

    def test_rehearsal_requires_exact_trusted_check_set(self):
        subset = tuple(
            {"name": name, "status": "PASS", "source": "TRUSTED_OBSERVED"}
            for name in REHEARSAL_REQUIRED_CHECKS
            if name != "scope"
        )
        with self.assertRaises(DomainError) as ctx:
            self.plan(self.context(checks=subset))
        self.assertEqual(ctx.exception.code, "DOMAIN_PREREQUISITE_MISSING")

    def test_rehearsal_id_and_ref_must_bind_run(self):
        with self.assertRaises(DomainError) as ctx:
            self.plan(
                self.context(
                    rehearsal_id="R-42-999-1",
                    rehearsal_ref="refs/tags/exp-rehearsal/42/R-42-999-1",
                )
            )
        self.assertEqual(ctx.exception.code, "DOMAIN_REHEARSAL_CONFLICT")


if __name__ == "__main__":
    unittest.main()
