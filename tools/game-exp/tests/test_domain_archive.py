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
    TrustedArchiveContext,
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
        "title": "Archive test",
        "operation_id": request_id,
        "parent": {"experiment": None, "commit": "a" * 40},
        "hypothesis": "Archive mutations remain recoverable and race-safe.",
        "success_criteria": ["Only REF_COMMITTED can archive."],
        "kill_criteria": ["Claimed archive can be aborted."],
        "scope": {"allowed": ["games/**"], "avoid": [".github/**"]},
        "runtime": {
            "godot": "n/a",
            "export_templates": "n/a",
            "addons_lock": "sha256:none",
        },
        "review": {"protocol": "blind-playtest-v1"},
        "created_at": "2026-09-24T16:50:00+08:00",
    }


class ArchiveDomainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.actor = TrustedActorContext(
            login="reviewer",
            user_id="101",
            permission="write",
        )
        mf = manifest()
        payload = build_operation_payload("experiment.bind", {"manifest": mf})
        plan = plan_domain_mutation(
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
        self.apply(plan)
        op_path = self.root / "operations/req_bind_42.json"
        op_path.parent.mkdir(parents=True, exist_ok=True)
        op_path.write_text(
            json.dumps(
                {
                    "request_id": "req_bind_42",
                    "payload_digest": digest_object(payload),
                    "payload": payload,
                    "domain_status": "APPLIED",
                    "domain_experiment_id": "EXP-42",
                    "domain_paths": plan.paths,
                }
            ),
            encoding="utf-8",
        )
        state_path = self.root / "experiments/EXP-42/state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["lifecycle"] = "INTEGRATED"
        state["sequence"] = 6
        state_path.write_text(json.dumps(state), encoding="utf-8")

    def apply(self, plan):
        for path, value in plan.writes.items():
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(value), encoding="utf-8")

    def context(
        self,
        action,
        *,
        archive_id=None,
        mode="ATOMIC_DELETE",
        ref_status=None,
        branch_sha=None,
        tag_target=None,
    ):
        return TrustedArchiveContext(
            action=action,
            experiment_id="EXP-42",
            archive_id=archive_id,
            mode=mode,
            branch_ref="refs/heads/exp/42",
            final_tag_ref="refs/tags/exp-final/42",
            expected_branch_sha="b" * 40,
            ref_status=ref_status,
            observed_branch_sha=branch_sha,
            observed_final_tag_target=tag_target,
        )

    def prepare(self, mode="ATOMIC_DELETE"):
        payload = build_operation_payload(
            "archive.prepare",
            {"experiment_id": "EXP-42", "mode": mode},
            actor_claim="reviewer",
        )
        plan = plan_domain_mutation(
            repo_dir=self.root,
            payload=payload,
            request_id="req_archive_prepare",
            payload_digest=digest_object(payload),
            repository_full_name="owner/repo",
            trusted_actor=self.actor,
            trusted_archive=self.context("PREPARE", mode=mode),
        )
        self.apply(plan)
        return plan

    def claim(self, archive_id="A-42-1", mode="ATOMIC_DELETE"):
        payload = build_operation_payload(
            "archive.claim",
            {"experiment_id": "EXP-42", "archive_id": archive_id},
        )
        plan = plan_domain_mutation(
            repo_dir=self.root,
            payload=payload,
            request_id="req_archive_claim",
            payload_digest=digest_object(payload),
            repository_full_name="owner/repo",
            trusted_archive=self.context("CLAIM", archive_id=archive_id, mode=mode),
        )
        self.apply(plan)
        return plan

    def observe(
        self,
        status,
        *,
        archive_id="A-42-1",
        mode="ATOMIC_DELETE",
        branch_sha=None,
        tag_target="b" * 40,
    ):
        payload = build_operation_payload(
            "archive.observe",
            {"experiment_id": "EXP-42", "archive_id": archive_id},
        )
        plan = plan_domain_mutation(
            repo_dir=self.root,
            payload=payload,
            request_id=f"req_archive_observe_{status.lower()}",
            payload_digest=digest_object(payload),
            repository_full_name="owner/repo",
            trusted_archive=self.context(
                "OBSERVE",
                archive_id=archive_id,
                mode=mode,
                ref_status=status,
                branch_sha=branch_sha,
                tag_target=tag_target,
            ),
        )
        self.apply(plan)
        return plan

    def commit(self, archive_id="A-42-1", mode="ATOMIC_DELETE", branch_sha=None):
        payload = build_operation_payload(
            "archive.commit",
            {"experiment_id": "EXP-42", "archive_id": archive_id},
        )
        plan = plan_domain_mutation(
            repo_dir=self.root,
            payload=payload,
            request_id="req_archive_commit",
            payload_digest=digest_object(payload),
            repository_full_name="owner/repo",
            trusted_archive=self.context(
                "COMMIT",
                archive_id=archive_id,
                mode=mode,
                ref_status="REF_COMMITTED",
                branch_sha=branch_sha,
                tag_target="b" * 40,
            ),
        )
        self.apply(plan)
        return plan

    def test_prepare_freezes_refs_and_locks_lifecycle_mutations(self):
        plan = self.prepare()
        archive = plan.writes["experiments/EXP-42/archives/A-42-1.json"]
        state = plan.writes["experiments/EXP-42/state.json"]
        self.assertEqual(archive["phase"], "PREPARED")
        self.assertEqual(archive["mode"], "ATOMIC_DELETE")
        self.assertEqual(archive["expected_branch_sha"], "b" * 40)
        self.assertEqual(archive["branch_ref"], "refs/heads/exp/42")
        self.assertEqual(archive["final_tag_ref"], "refs/tags/exp-final/42")
        self.assertEqual(state["archive_lock"]["archive_id"], "A-42-1")
        self.assertEqual(state["lifecycle"], "INTEGRATED")

    def test_abort_is_allowed_only_before_claim(self):
        self.prepare()
        payload = build_operation_payload(
            "archive.abort",
            {
                "experiment_id": "EXP-42",
                "archive_id": "A-42-1",
                "reason": "operator cancelled before claim",
            },
            actor_claim="reviewer",
        )
        plan = plan_domain_mutation(
            repo_dir=self.root,
            payload=payload,
            request_id="req_archive_abort",
            payload_digest=digest_object(payload),
            repository_full_name="owner/repo",
            trusted_actor=self.actor,
        )
        self.apply(plan)
        self.assertEqual(
            plan.writes["experiments/EXP-42/archives/A-42-1.json"]["phase"],
            "ABORTED",
        )
        self.assertIsNone(plan.writes["experiments/EXP-42/state.json"]["archive_lock"])

    def test_claim_blocks_abort(self):
        self.prepare()
        self.claim()
        payload = build_operation_payload(
            "archive.abort",
            {
                "experiment_id": "EXP-42",
                "archive_id": "A-42-1",
                "reason": "too late",
            },
            actor_claim="reviewer",
        )
        with self.assertRaises(DomainError) as ctx:
            plan_domain_mutation(
                repo_dir=self.root,
                payload=payload,
                request_id="req_archive_abort_late",
                payload_digest=digest_object(payload),
                repository_full_name="owner/repo",
                trusted_actor=self.actor,
            )
        self.assertEqual(ctx.exception.code, "DOMAIN_ARCHIVE_CONFLICT")

    def test_ref_conflict_cannot_commit_and_keeps_lock(self):
        self.prepare()
        self.claim()
        plan = self.observe(
            "REF_CONFLICT",
            branch_sha="c" * 40,
            tag_target=None,
        )
        self.assertEqual(
            plan.writes["experiments/EXP-42/archives/A-42-1.json"]["phase"],
            "REF_CONFLICT",
        )
        self.assertEqual(
            plan.writes["experiments/EXP-42/state.json"]["archive_lock"]["phase"],
            "REF_CONFLICT",
        )
        with self.assertRaises(DomainError) as ctx:
            self.commit()
        self.assertEqual(ctx.exception.code, "DOMAIN_ARCHIVE_CONFLICT")

    def test_atomic_delete_ref_commit_archives_and_clears_lock(self):
        self.prepare()
        self.claim()
        self.observe("REF_COMMITTED", branch_sha=None, tag_target="b" * 40)
        plan = self.commit(branch_sha=None)
        archive = plan.writes["experiments/EXP-42/archives/A-42-1.json"]
        state = plan.writes["experiments/EXP-42/state.json"]
        self.assertEqual(archive["phase"], "COMMITTED")
        self.assertEqual(state["lifecycle"], "ARCHIVED")
        self.assertEqual(state["sequence"], 7)
        self.assertIsNone(state["archive_lock"])
        self.assertEqual(state["current_archive_id"], "A-42-1")

    def test_retain_branch_commit_records_retained_branch(self):
        self.prepare("RETAIN_BRANCH")
        self.claim(mode="RETAIN_BRANCH")
        self.observe(
            "REF_COMMITTED",
            mode="RETAIN_BRANCH",
            branch_sha="b" * 40,
            tag_target="b" * 40,
        )
        plan = self.commit(mode="RETAIN_BRANCH", branch_sha="b" * 40)
        archive = plan.writes["experiments/EXP-42/archives/A-42-1.json"]
        self.assertEqual(archive["mode"], "RETAIN_BRANCH")
        self.assertEqual(
            archive["final_ref_observation"]["branch_sha"],
            "b" * 40,
        )

    def test_prepare_rejects_context_mode_mismatch(self):
        payload = build_operation_payload(
            "archive.prepare",
            {"experiment_id": "EXP-42", "mode": "ATOMIC_DELETE"},
            actor_claim="reviewer",
        )
        with self.assertRaises(DomainError) as ctx:
            plan_domain_mutation(
                repo_dir=self.root,
                payload=payload,
                request_id="req_archive_prepare_bad",
                payload_digest=digest_object(payload),
                repository_full_name="owner/repo",
                trusted_actor=self.actor,
                trusted_archive=self.context("PREPARE", mode="RETAIN_BRANCH"),
            )
        self.assertEqual(ctx.exception.code, "DOMAIN_ARCHIVE_CONFLICT")


if __name__ == "__main__":
    unittest.main()
