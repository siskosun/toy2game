from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))

from domain_core import (  # noqa: E402
    DomainError,
    TrustedBindingContext,
    plan_domain_mutation,
)
from protocol_core import build_operation_payload, digest_object  # noqa: E402


def manifest(request_id="req_bind_1"):
    return {
        "schema_version": 1,
        "experiment": {
            "host": "github.com",
            "repository_id": "1384446218",
            "issue_id": "2000000001",
            "issue_number": "123",
        },
        "title": "Three role combat",
        "subject": {
            "type": "game-prototype",
            "id": "three-role-combat",
            "name": "Three Role Combat",
            "root_path": "games/three-role-combat",
        },
        "operation_id": request_id,
        "parent": {"experiment": None, "commit": "a" * 40},
        "hypothesis": "Three roles improve readability.",
        "success_criteria": ["Roles are distinguishable."],
        "kill_criteria": ["Players cannot explain role differences."],
        "scope": {"allowed": ["games/**"], "avoid": ["infra/**"]},
        "runtime": {
            "godot": "n/a-toy2game-pilot",
            "export_templates": "n/a",
            "addons_lock": "sha256:none",
        },
        "review": {"protocol": "blind-playtest-v1"},
        "created_at": "2026-09-24T12:00:00+08:00",
    }


class DomainBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ctx = TrustedBindingContext(
            host="github.com",
            repository_id="1384446218",
            issue_id="2000000001",
            issue_number="123",
            parent_sha="a" * 40,
        )

    def plan(self, value=None, request_id="req_bind_1"):
        value = value or manifest(request_id)
        payload = build_operation_payload("experiment.bind", {"manifest": value})
        return plan_domain_mutation(
            repo_dir=self.root,
            payload=payload,
            request_id=request_id,
            payload_digest=digest_object(payload),
            repository_full_name="siskosun/toy2game",
            trusted_binding=self.ctx,
        )

    def test_binding_persists_reconstructable_manifest_and_canonical_refs(self):
        plan = self.plan()
        self.assertEqual(plan.status, "APPLIED")
        self.assertEqual(plan.experiment_id, "EXP-123")
        self.assertEqual(
            set(plan.paths),
            {
                "experiments/EXP-123/binding.json",
                "experiments/EXP-123/manifest.json",
                "experiments/EXP-123/state.json",
            },
        )
        binding = plan.writes["experiments/EXP-123/binding.json"]
        init = binding["initialization"]
        self.assertEqual(init["branch_ref"], "refs/heads/exp/123")
        self.assertEqual(init["base_tag_ref"], "refs/tags/exp-base/123")
        self.assertEqual(init["final_tag_ref"], "refs/tags/exp-final/123")
        self.assertEqual(init["manifest_path"], "experiments/EXP-123/manifest.yaml")
        self.assertEqual(
            init["manifest_digest"],
            digest_object(plan.writes["experiments/EXP-123/manifest.json"]),
        )

    def test_validation_and_planning_do_not_mutate_manifest_or_payload(self):
        import copy

        value = manifest()
        original_manifest = copy.deepcopy(value)
        payload = build_operation_payload("experiment.bind", {"manifest": value})
        original_payload = copy.deepcopy(payload)
        before = digest_object(payload)

        plan_domain_mutation(
            repo_dir=self.root,
            payload=payload,
            request_id="req_bind_1",
            payload_digest=before,
            repository_full_name="siskosun/toy2game",
            trusted_binding=self.ctx,
        )

        self.assertEqual(value, original_manifest)
        self.assertEqual(payload, original_payload)
        self.assertEqual(digest_object(payload), before)
        self.assertEqual(value["scope"]["allowed"], ["games/**"])
        self.assertEqual(value["scope"]["avoid"], ["infra/**"])

    def test_legacy_manifest_without_subject_remains_valid(self):
        value = manifest()
        value.pop("subject")
        plan = self.plan(value)
        self.assertEqual(plan.status, "APPLIED")
        self.assertNotIn(
            "subject",
            plan.writes["experiments/EXP-123/manifest.json"],
        )

    def test_subject_is_persisted_without_mutation(self):
        value = manifest()
        plan = self.plan(value)
        stored = plan.writes["experiments/EXP-123/manifest.json"]
        self.assertEqual(stored["subject"], value["subject"])

    def test_subject_rejects_glob_root_path(self):
        value = manifest()
        value["subject"]["root_path"] = "games/three-role-*"
        with self.assertRaisesRegex(DomainError, "concrete path"):
            self.plan(value)

    def test_repository_subject_requires_canonical_identity(self):
        value = manifest()
        value["subject"] = {
            "type": "repository",
            "id": "repo",
            "name": "Repository-wide",
            "root_path": ".",
        }
        with self.assertRaisesRegex(DomainError, "id=repository"):
            self.plan(value)

    def test_trusted_issue_identity_mismatch_is_rejected(self):
        value = manifest()
        value["experiment"]["issue_id"] = "999"
        with self.assertRaisesRegex(DomainError, "trusted identity mismatch"):
            self.plan(value)

    def test_trusted_parent_mismatch_is_rejected(self):
        value = manifest()
        value["parent"]["commit"] = "b" * 40
        with self.assertRaisesRegex(DomainError, "trusted parent SHA"):
            self.plan(value)

    def test_operation_id_must_bind_request_id(self):
        value = manifest("req_other")
        with self.assertRaisesRegex(DomainError, "manifest.operation_id"):
            self.plan(value, request_id="req_bind_1")

    def test_existing_authoritative_domain_state_conflicts(self):
        path = self.root / "experiments/EXP-123"
        path.mkdir(parents=True)
        (path / "binding.json").write_text("{}\n", encoding="utf-8")
        with self.assertRaises(DomainError) as ctx:
            self.plan()
        self.assertEqual(ctx.exception.code, "EXPERIMENT_IDENTITY_CONFLICT")

    def test_non_domain_operation_remains_request_only(self):
        payload = build_operation_payload("transport.probe", {"value": "x"})
        plan = plan_domain_mutation(
            repo_dir=self.root,
            payload=payload,
            request_id="req_probe",
            payload_digest=digest_object(payload),
            repository_full_name="siskosun/toy2game",
            trusted_binding=None,
        )
        self.assertEqual(plan.status, "REQUEST_ONLY")
        self.assertEqual(plan.writes, {})


if __name__ == "__main__":
    unittest.main()
