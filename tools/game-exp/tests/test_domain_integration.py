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
    TrustedIntegrationContext,
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
        "title": "Integration test",
        "operation_id": request_id,
        "parent": {"experiment": None, "commit": "a" * 40},
        "hypothesis": "Selected Candidate integrates through an exact reviewed tree.",
        "success_criteria": ["Merged PR tree equals trusted Rehearsal tree."],
        "kill_criteria": ["A different tree can be registered as integrated."],
        "scope": {"allowed": ["games/**"], "avoid": [".github/**"]},
        "runtime": {
            "godot": "n/a",
            "export_templates": "n/a",
            "addons_lock": "sha256:none",
        },
        "review": {"protocol": "blind-playtest-v1"},
        "created_at": "2026-09-24T16:00:00+08:00",
    }


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        mf = manifest()
        payload = build_operation_payload("experiment.bind", {"manifest": mf})
        bind = plan_domain_mutation(
            repo_dir=self.root,
            payload=payload,
            request_id="req_bind_42",
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
        op_path = self.root / "operations/req_bind_42.json"
        op_path.parent.mkdir(parents=True, exist_ok=True)
        op_path.write_text(
            json.dumps(
                {
                    "domain_experiment_id": "EXP-42",
                    "domain_paths": bind.paths,
                    "domain_status": "APPLIED",
                    "payload": payload,
                    "payload_digest": digest_object(payload),
                    "request_id": "req_bind_42",
                }
            ),
            encoding="utf-8",
        )

        self.candidate_id = "C-42-123-1"
        self.rehearsal_id = "R-42-456-1"
        self.tree = "3" * 40
        candidate_path = self.root / f"experiments/EXP-42/candidates/{self.candidate_id}.json"
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        candidate_path.write_text(
            json.dumps(
                {
                    "kind": "candidate",
                    "candidate_id": self.candidate_id,
                    "experiment_id": "EXP-42",
                    "source_sha": "b" * 40,
                    "artifact_digest": "sha256:" + "c" * 64,
                }
            ),
            encoding="utf-8",
        )
        rehearsal_path = self.root / f"experiments/EXP-42/rehearsals/{self.rehearsal_id}.json"
        rehearsal_path.parent.mkdir(parents=True, exist_ok=True)
        rehearsal_path.write_text(
            json.dumps(
                {
                    "kind": "rehearsal",
                    "rehearsal_id": self.rehearsal_id,
                    "experiment_id": "EXP-42",
                    "candidate_id": self.candidate_id,
                    "main_sha": "1" * 40,
                    "source_sha": "b" * 40,
                    "integration_sha": "2" * 40,
                    "integration_tree_sha": self.tree,
                }
            ),
            encoding="utf-8",
        )
        state_path = self.root / "experiments/EXP-42/state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state.update(
            {
                "lifecycle": "SELECTED",
                "sequence": 5,
                "last_decision_id": "req_selected",
                "current_candidate_id": self.candidate_id,
                "current_rehearsal_id": self.rehearsal_id,
                "current_rehearsal_candidate_id": self.candidate_id,
                "current_rehearsal_main_sha": "1" * 40,
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
            "rehearsal_id": self.rehearsal_id,
            "integration_id": "I-42-PR-77",
            "pr_number": "77",
            "pr_id": "90077",
            "pr_url": "https://github.com/owner/repo/pull/77",
            "head_ref": f"game-exp/integration/42/{self.rehearsal_id}",
            "head_sha": "4" * 40,
            "head_tree_sha": self.tree,
            "merge_sha": "5" * 40,
            "merge_tree_sha": self.tree,
            "merged_at": "2026-09-24T16:30:00Z",
            "merged_by_login": "reviewer",
            "merged_by_user_id": "101",
            "workflow_source_sha": "6" * 40,
            "run_id": "789",
            "run_attempt": "1",
        }
        values.update(overrides)
        return TrustedIntegrationContext(**values)

    def plan(self, ctx=None):
        payload = build_operation_payload(
            "integration.register",
            {"experiment_id": "EXP-42"},
            preconditions={
                "candidate_id": self.candidate_id,
                "rehearsal_id": self.rehearsal_id,
                "pr_number": "77",
            },
        )
        return plan_domain_mutation(
            repo_dir=self.root,
            payload=payload,
            request_id="req_integration_77",
            payload_digest=digest_object(payload),
            repository_full_name="owner/repo",
            trusted_integration=ctx if ctx is not None else self.context(),
        )

    def test_integration_registers_exact_rehearsal_tree_and_marks_integrated(self):
        plan = self.plan()
        state = plan.writes["experiments/EXP-42/state.json"]
        record = plan.writes["experiments/EXP-42/integrations/I-42-PR-77.json"]
        self.assertEqual(state["lifecycle"], "INTEGRATED")
        self.assertEqual(state["sequence"], 6)
        self.assertEqual(state["current_integration_id"], "I-42-PR-77")
        self.assertEqual(record["candidate_id"], self.candidate_id)
        self.assertEqual(record["rehearsal_id"], self.rehearsal_id)
        self.assertEqual(record["pr"]["head_tree_sha"], self.tree)
        self.assertEqual(record["pr"]["merge_tree_sha"], self.tree)

    def test_integration_requires_selected(self):
        state_path = self.root / "experiments/EXP-42/state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["lifecycle"] = "PROMISING"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaises(DomainError) as ctx:
            self.plan()
        self.assertEqual(ctx.exception.code, "DOMAIN_INVALID_TRANSITION")

    def test_integration_rejects_tree_drift(self):
        with self.assertRaises(DomainError) as ctx:
            self.plan(self.context(merge_tree_sha="9" * 40))
        self.assertEqual(ctx.exception.code, "DOMAIN_INTEGRATION_CONFLICT")

    def test_integration_rejects_stale_rehearsal_identity(self):
        with self.assertRaises(DomainError) as ctx:
            self.plan(
                self.context(
                    rehearsal_id="R-42-999-1",
                    head_ref="game-exp/integration/42/R-42-999-1",
                )
            )
        self.assertEqual(ctx.exception.code, "DOMAIN_INTEGRATION_CONFLICT")

    def test_integration_id_binds_pr_number(self):
        with self.assertRaises(DomainError) as ctx:
            self.plan(self.context(integration_id="I-42-PR-88"))
        self.assertEqual(ctx.exception.code, "DOMAIN_INTEGRATION_CONFLICT")


if __name__ == "__main__":
    unittest.main()
