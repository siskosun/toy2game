from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))

from domain_core import DomainError  # noqa: E402
from trusted_writer import resolve_trusted_actor, resolve_trusted_archive, resolve_trusted_binding, resolve_trusted_candidate, resolve_trusted_integration, resolve_trusted_rehearsal, resolve_trusted_retention, resolve_trusted_selection_rehearsal  # noqa: E402


def payload():
    return {
        "kind": "operation_request",
        "schema_version": 1,
        "operation": "experiment.bind",
        "input": {
            "manifest": {
                "schema_version": 1,
                "experiment": {
                    "host": "github.com",
                    "repository_id": "1384446218",
                    "issue_id": "2000000001",
                    "issue_number": "123",
                },
                "title": "Three role combat",
                "operation_id": "req_bind_1",
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
        },
        "preconditions": {},
    }


class TrustedResolverTests(unittest.TestCase):
    @patch("trusted_writer.github_json")
    def test_resolver_uses_repo_issue_and_commit_facts(self, api):
        api.side_effect = [
            {"id": 1384446218, "full_name": "siskosun/toy2game"},
            {"id": 2000000001, "number": 123},
            {"sha": "a" * 40},
        ]
        ctx = resolve_trusted_binding("siskosun/toy2game", payload())
        self.assertEqual(ctx.repository_id, "1384446218")
        self.assertEqual(ctx.issue_id, "2000000001")
        self.assertEqual(ctx.issue_number, "123")
        self.assertEqual(ctx.parent_sha, "a" * 40)

    @patch("trusted_writer.github_json")
    def test_pull_request_cannot_be_bound_as_experiment_issue(self, api):
        api.side_effect = [
            {"id": 1384446218},
            {"id": 2000000001, "number": 123, "pull_request": {"url": "x"}},
        ]
        with self.assertRaisesRegex(DomainError, "not a pull request"):
            resolve_trusted_binding("siskosun/toy2game", payload())

    def test_candidate_register_requires_candidate_authority(self):
        payload = {
            "kind": "operation_request",
            "operation": "candidate.register",
            "input": {"experiment_id": "EXP-21"},
        }
        with self.assertRaises(DomainError) as ctx:
            resolve_trusted_candidate(
                payload,
                authority="request",
                context_path=None,
            )
        self.assertEqual(ctx.exception.code, "DOMAIN_AUTHORIZATION_FAILED")

    def test_candidate_context_file_is_loaded_only_with_candidate_authority(self):
        import json
        import tempfile

        payload = {
            "kind": "operation_request",
            "operation": "candidate.register",
            "input": {"experiment_id": "EXP-21"},
        }
        value = {
            "experiment_id": "EXP-21",
            "candidate_id": "C-21-123-1",
            "source_sha": "a" * 40,
            "manifest_digest": "sha256:" + "b" * 64,
            "artifact_digest": "sha256:" + "c" * 64,
            "policy_digest": "sha256:" + "d" * 64,
            "workflow_source_sha": "e" * 40,
            "run_id": "123",
            "run_attempt": "1",
            "checks": [{"name": "build", "status": "PASS", "source": "TRUSTED_OBSERVED"}],
            "retention": {
                "provider": "github-immutable-release",
                "release_tag": "game-exp-candidate-21-123-1",
                "release_url": "https://github.com/owner/repo/releases/tag/game-exp-candidate-21-123-1",
                "immutable": True,
                "artifact_digest": "sha256:" + "c" * 64,
            },
            "attestation": {
                "provider": "github-artifact-attestations",
                "verified": True,
                "subject_digest": "sha256:" + "c" * 64,
                "source_sha": "a" * 40,
            },
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "candidate.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            ctx = resolve_trusted_candidate(
                payload,
                authority="candidate",
                context_path=str(path),
            )
        self.assertEqual(ctx.candidate_id, "C-21-123-1")
        self.assertEqual(ctx.run_id, "123")
        self.assertEqual(ctx.checks[0]["source"], "TRUSTED_OBSERVED")

    @patch("trusted_writer.github_json")
    def test_promising_retention_resolver_uses_live_immutable_release(self, api):
        import json
        import tempfile

        payload = {
            "kind": "operation_request",
            "operation": "experiment.decision",
            "input": {
                "experiment_id": "EXP-21",
                "to_state": "PROMISING",
                "previous_decision_id": "req_review_state",
                "reason": "promote",
            },
        }
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            candidate_id = "C-21-123-1"
            state_path = root / "experiments/EXP-21/state.json"
            candidate_path = root / f"experiments/EXP-21/candidates/{candidate_id}.json"
            state_path.parent.mkdir(parents=True, exist_ok=True)
            candidate_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(
                json.dumps({"current_candidate_id": candidate_id}),
                encoding="utf-8",
            )
            candidate_path.write_text(
                json.dumps(
                    {
                        "source_sha": "a" * 40,
                        "artifact_digest": "sha256:" + "c" * 64,
                        "retention": {
                            "release_tag": "game-exp-candidate-21-123-1",
                        },
                    }
                ),
                encoding="utf-8",
            )
            api.return_value = {
                "id": 9001,
                "tag_name": "game-exp-candidate-21-123-1",
                "html_url": "https://github.com/owner/repo/releases/tag/game-exp-candidate-21-123-1",
                "immutable": True,
                "draft": False,
                "prerelease": False,
                "target_commitish": "a" * 40,
                "assets": [
                    {
                        "id": 8001,
                        "name": "candidate.tgz",
                        "state": "uploaded",
                        "digest": "sha256:" + "c" * 64,
                    }
                ],
            }
            ctx = resolve_trusted_retention("owner/repo", payload, root)
        self.assertEqual(ctx.candidate_id, "C-21-123-1")
        self.assertEqual(ctx.release_id, "9001")
        self.assertEqual(ctx.asset_id, "8001")
        self.assertTrue(ctx.immutable)
        self.assertEqual(ctx.artifact_digest, "sha256:" + "c" * 64)

    @patch("trusted_writer.github_json")
    def test_promising_retention_resolver_rejects_mutable_release(self, api):
        import json
        import tempfile

        payload = {
            "kind": "operation_request",
            "operation": "experiment.decision",
            "input": {
                "experiment_id": "EXP-21",
                "to_state": "PROMISING",
                "previous_decision_id": "req_review_state",
                "reason": "promote",
            },
        }
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            candidate_id = "C-21-123-1"
            state_path = root / "experiments/EXP-21/state.json"
            candidate_path = root / f"experiments/EXP-21/candidates/{candidate_id}.json"
            state_path.parent.mkdir(parents=True, exist_ok=True)
            candidate_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps({"current_candidate_id": candidate_id}), encoding="utf-8")
            candidate_path.write_text(
                json.dumps(
                    {
                        "source_sha": "a" * 40,
                        "artifact_digest": "sha256:" + "c" * 64,
                        "retention": {"release_tag": "game-exp-candidate-21-123-1"},
                    }
                ),
                encoding="utf-8",
            )
            api.return_value = {
                "id": 9001,
                "tag_name": "game-exp-candidate-21-123-1",
                "html_url": "https://github.com/owner/repo/releases/tag/game-exp-candidate-21-123-1",
                "immutable": False,
                "draft": False,
                "prerelease": False,
                "target_commitish": "a" * 40,
                "assets": [
                    {
                        "id": 8001,
                        "name": "candidate.tgz",
                        "state": "uploaded",
                        "digest": "sha256:" + "c" * 64,
                    }
                ],
            }
            with self.assertRaises(DomainError) as ctx:
                resolve_trusted_retention("owner/repo", payload, root)
        self.assertEqual(ctx.exception.code, "DOMAIN_PREREQUISITE_MISSING")

    def _rehearsal_context_value(self):
        return {
            "experiment_id": "EXP-21",
            "candidate_id": "C-21-123-1",
            "rehearsal_id": "R-21-456-1",
            "main_sha": "a" * 40,
            "source_sha": "b" * 40,
            "integration_sha": "c" * 40,
            "integration_tree_sha": "d" * 40,
            "rehearsal_ref": "refs/tags/exp-rehearsal/21/R-21-456-1",
            "workflow_source_sha": "e" * 40,
            "run_id": "456",
            "run_attempt": "1",
            "policy_digest": "sha256:" + "f" * 64,
            "scope_digest": "sha256:" + "6" * 64,
            "checks": [
                {"name": "merge", "status": "PASS", "source": "TRUSTED_OBSERVED"},
            ],
        }

    def _rehearsal_api_evidence(self, value=None):
        value = value or self._rehearsal_context_value()
        message = "\n".join(
            [
                f"game-exp-experiment: {value['experiment_id']}",
                f"game-exp-candidate-id: {value['candidate_id']}",
                f"game-exp-rehearsal-id: {value['rehearsal_id']}",
                f"game-exp-main-sha: {value['main_sha']}",
                f"game-exp-source-sha: {value['source_sha']}",
                f"game-exp-integration-sha: {value['integration_sha']}",
                f"game-exp-integration-tree-sha: {value['integration_tree_sha']}",
                f"game-exp-workflow-source-sha: {value['workflow_source_sha']}",
                f"game-exp-run-id: {value['run_id']}",
                f"game-exp-run-attempt: {value['run_attempt']}",
                f"game-exp-policy-digest: {value['policy_digest']}",
                f"game-exp-scope-digest: {value['scope_digest']}",
            ]
        )
        return [
            {"object": {"type": "tag", "sha": "1" * 40}},
            {
                "object": {"type": "commit", "sha": value["integration_sha"]},
                "message": message,
            },
            {
                "tree": {"sha": value["integration_tree_sha"]},
                "parents": [
                    {"sha": value["main_sha"]},
                    {"sha": value["source_sha"]},
                ],
            },
            {"object": {"sha": value["main_sha"]}},
            {
                "run_attempt": 1,
                "event": "workflow_dispatch",
                "path": ".github/workflows/game-exp-rehearsal.yml",
                "head_sha": value["workflow_source_sha"],
                "status": "in_progress",
                "conclusion": None,
            },
        ]

    @patch("trusted_writer.github_json")
    def test_rehearsal_resolver_rechecks_anchor_tree_main_and_run(self, api):
        import json
        import tempfile

        value = self._rehearsal_context_value()
        api.side_effect = self._rehearsal_api_evidence(value)
        payload = {
            "kind": "operation_request",
            "operation": "rehearsal.register",
            "input": {"experiment_id": "EXP-21"},
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "rehearsal.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            ctx = resolve_trusted_rehearsal(
                "owner/repo",
                payload,
                authority="rehearsal",
                context_path=str(path),
            )
        self.assertEqual(ctx.rehearsal_id, "R-21-456-1")
        self.assertEqual(ctx.integration_tree_sha, "d" * 40)
        self.assertEqual(ctx.main_sha, "a" * 40)
        self.assertEqual(api.call_count, 5)

    @patch("trusted_writer.github_json")
    def test_rehearsal_resolver_rejects_stale_main(self, api):
        import json
        import tempfile

        value = self._rehearsal_context_value()
        evidence = self._rehearsal_api_evidence(value)
        evidence[3] = {"object": {"sha": "9" * 40}}
        api.side_effect = evidence
        payload = {
            "kind": "operation_request",
            "operation": "rehearsal.register",
            "input": {"experiment_id": "EXP-21"},
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "rehearsal.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(DomainError) as ctx:
                resolve_trusted_rehearsal(
                    "owner/repo",
                    payload,
                    authority="rehearsal",
                    context_path=str(path),
                )
        self.assertEqual(ctx.exception.code, "DOMAIN_REHEARSAL_CONFLICT")

    @patch("trusted_writer.github_json")
    def test_rehearsal_resolver_rejects_wrong_integration_parents(self, api):
        import json
        import tempfile

        value = self._rehearsal_context_value()
        evidence = self._rehearsal_api_evidence(value)
        evidence[2]["parents"] = [
            {"sha": value["source_sha"]},
            {"sha": value["main_sha"]},
        ]
        api.side_effect = evidence
        payload = {
            "kind": "operation_request",
            "operation": "rehearsal.register",
            "input": {"experiment_id": "EXP-21"},
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "rehearsal.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(DomainError) as ctx:
                resolve_trusted_rehearsal(
                    "owner/repo",
                    payload,
                    authority="rehearsal",
                    context_path=str(path),
                )
        self.assertEqual(ctx.exception.code, "DOMAIN_REHEARSAL_CONFLICT")

    def _write_selection_rehearsal_fixture(self, root, value=None):
        import json

        value = value or self._rehearsal_context_value()
        state_path = root / "experiments/EXP-21/state.json"
        rehearsal_path = root / f"experiments/EXP-21/rehearsals/{value['rehearsal_id']}.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        rehearsal_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(
                {
                    "experiment_id": "EXP-21",
                    "lifecycle": "PROMISING",
                    "current_candidate_id": value["candidate_id"],
                    "current_rehearsal_id": value["rehearsal_id"],
                    "current_rehearsal_candidate_id": value["candidate_id"],
                    "current_rehearsal_main_sha": value["main_sha"],
                }
            ),
            encoding="utf-8",
        )
        rehearsal_path.write_text(
            json.dumps(
                {
                    "kind": "rehearsal",
                    "experiment_id": value["experiment_id"],
                    "candidate_id": value["candidate_id"],
                    "rehearsal_id": value["rehearsal_id"],
                    "main_sha": value["main_sha"],
                    "source_sha": value["source_sha"],
                    "integration_sha": value["integration_sha"],
                    "integration_tree_sha": value["integration_tree_sha"],
                    "rehearsal_ref": value["rehearsal_ref"],
                    "workflow_source_sha": value["workflow_source_sha"],
                    "github_run_id": value["run_id"],
                    "github_run_attempt": value["run_attempt"],
                    "policy_digest": value["policy_digest"],
                    "scope_digest": value["scope_digest"],
                    "checks": value["checks"],
                }
            ),
            encoding="utf-8",
        )

    def _selected_payload(self):
        return {
            "kind": "operation_request",
            "operation": "experiment.decision",
            "input": {
                "experiment_id": "EXP-21",
                "to_state": "SELECTED",
                "previous_decision_id": "req_promising",
                "reason": "select",
            },
        }

    @patch("trusted_writer.github_json")
    def test_selection_rechecks_completed_successful_current_rehearsal(self, api):
        import tempfile

        value = self._rehearsal_context_value()
        evidence = self._rehearsal_api_evidence(value)
        evidence[4]["status"] = "completed"
        evidence[4]["conclusion"] = "success"
        api.side_effect = evidence
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_selection_rehearsal_fixture(root, value)
            ctx = resolve_trusted_selection_rehearsal(
                "owner/repo",
                self._selected_payload(),
                root,
            )
        self.assertEqual(ctx.rehearsal_id, value["rehearsal_id"])
        self.assertEqual(ctx.integration_tree_sha, value["integration_tree_sha"])
        self.assertEqual(api.call_count, 5)

    @patch("trusted_writer.github_json")
    def test_selection_rejects_rehearsal_if_main_advanced(self, api):
        import tempfile

        value = self._rehearsal_context_value()
        evidence = self._rehearsal_api_evidence(value)
        evidence[3] = {"object": {"sha": "9" * 40}}
        evidence[4]["status"] = "completed"
        evidence[4]["conclusion"] = "success"
        api.side_effect = evidence
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_selection_rehearsal_fixture(root, value)
            with self.assertRaises(DomainError) as ctx:
                resolve_trusted_selection_rehearsal(
                    "owner/repo",
                    self._selected_payload(),
                    root,
                )
        self.assertEqual(ctx.exception.code, "DOMAIN_REHEARSAL_CONFLICT")

    @patch("trusted_writer.github_json")
    def test_selection_rejects_failed_rehearsal_run(self, api):
        import tempfile

        value = self._rehearsal_context_value()
        evidence = self._rehearsal_api_evidence(value)
        evidence[4]["status"] = "completed"
        evidence[4]["conclusion"] = "failure"
        api.side_effect = evidence
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_selection_rehearsal_fixture(root, value)
            with self.assertRaises(DomainError) as ctx:
                resolve_trusted_selection_rehearsal(
                    "owner/repo",
                    self._selected_payload(),
                    root,
                )
        self.assertEqual(ctx.exception.code, "DOMAIN_REHEARSAL_CONFLICT")

    def _integration_context_value(self):
        return {
            "experiment_id": "EXP-21",
            "candidate_id": "C-21-123-1",
            "rehearsal_id": "R-21-456-1",
            "integration_id": "I-21-PR-77",
            "pr_number": "77",
            "pr_id": "9001",
            "pr_url": "https://github.com/owner/repo/pull/77",
            "head_ref": "game-exp/integration/21/R-21-456-1",
            "head_sha": "a" * 40,
            "head_tree_sha": "b" * 40,
            "merge_sha": "c" * 40,
            "merge_tree_sha": "b" * 40,
            "merged_at": "2026-09-24T16:30:00Z",
            "merged_by_login": "reviewer",
            "merged_by_user_id": "101",
            "workflow_source_sha": "d" * 40,
            "run_id": "789",
            "run_attempt": "1",
        }

    @patch("trusted_writer.github_json")
    def test_integration_resolver_rechecks_pr_trees_ancestry_and_workflow(self, api):
        import json
        import tempfile

        value = self._integration_context_value()
        api.side_effect = [
            {
                "id": 9001,
                "html_url": value["pr_url"],
                "merged": True,
                "merged_at": value["merged_at"],
                "merged_by": {"login": "reviewer", "id": 101},
                "base": {"ref": "main"},
                "head": {
                    "ref": value["head_ref"],
                    "sha": value["head_sha"],
                    "repo": {"full_name": "owner/repo"},
                },
                "merge_commit_sha": value["merge_sha"],
            },
            {"tree": {"sha": value["head_tree_sha"]}},
            {"tree": {"sha": value["merge_tree_sha"]}},
            {
                "status": "ahead",
                "merge_base_commit": {"sha": value["merge_sha"]},
            },
            {
                "run_attempt": 1,
                "event": "workflow_dispatch",
                "path": ".github/workflows/game-exp-integration-finalize.yml",
                "head_sha": value["workflow_source_sha"],
                "status": "in_progress",
            },
        ]
        payload = {
            "kind": "operation_request",
            "operation": "integration.register",
            "input": {"experiment_id": "EXP-21"},
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "integration.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            ctx = resolve_trusted_integration(
                "owner/repo",
                payload,
                authority="integration",
                context_path=str(path),
            )
        self.assertEqual(ctx.integration_id, "I-21-PR-77")
        self.assertEqual(ctx.merge_tree_sha, "b" * 40)
        self.assertEqual(api.call_count, 5)

    @patch("trusted_writer.github_json")
    def test_integration_resolver_rejects_merge_not_in_main_history(self, api):
        import json
        import tempfile

        value = self._integration_context_value()
        api.side_effect = [
            {
                "id": 9001,
                "html_url": value["pr_url"],
                "merged": True,
                "merged_at": value["merged_at"],
                "merged_by": {"login": "reviewer", "id": 101},
                "base": {"ref": "main"},
                "head": {
                    "ref": value["head_ref"],
                    "sha": value["head_sha"],
                    "repo": {"full_name": "owner/repo"},
                },
                "merge_commit_sha": value["merge_sha"],
            },
            {"tree": {"sha": value["head_tree_sha"]}},
            {"tree": {"sha": value["merge_tree_sha"]}},
            {
                "status": "diverged",
                "merge_base_commit": {"sha": "9" * 40},
            },
        ]
        payload = {
            "kind": "operation_request",
            "operation": "integration.register",
            "input": {"experiment_id": "EXP-21"},
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "integration.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(DomainError) as ctx:
                resolve_trusted_integration(
                    "owner/repo",
                    payload,
                    authority="integration",
                    context_path=str(path),
                )
        self.assertEqual(ctx.exception.code, "DOMAIN_INTEGRATION_CONFLICT")

    def _write_archive_fixture(self, root, *, mode="ATOMIC_DELETE", phase="CLAIMED"):
        import json

        experiment_id = "EXP-21"
        base = root / f"experiments/{experiment_id}"
        base.mkdir(parents=True, exist_ok=True)
        (base / "binding.json").write_text(
            json.dumps(
                {
                    "kind": "experiment_identity",
                    "experiment_id": experiment_id,
                    "initialization": {
                        "branch_ref": "refs/heads/exp/21",
                        "final_tag_ref": "refs/tags/exp-final/21",
                    },
                }
            ),
            encoding="utf-8",
        )
        archives = base / "archives"
        archives.mkdir(parents=True, exist_ok=True)
        (archives / "A-21-1.json").write_text(
            json.dumps(
                {
                    "kind": "archive_operation",
                    "archive_id": "A-21-1",
                    "experiment_id": experiment_id,
                    "phase": phase,
                    "mode": mode,
                    "expected_branch_sha": "b" * 40,
                    "branch_ref": "refs/heads/exp/21",
                    "final_tag_ref": "refs/tags/exp-final/21",
                }
            ),
            encoding="utf-8",
        )

    def _archive_payload(self, operation, **extra):
        return {
            "kind": "operation_request",
            "operation": operation,
            "input": {"experiment_id": "EXP-21", **extra},
        }

    @patch("trusted_writer.github_json_optional")
    def test_archive_prepare_freezes_live_branch_sha(self, optional):
        import json
        import tempfile

        optional.side_effect = [
            {"object": {"type": "commit", "sha": "b" * 40}},
            None,
        ]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            base = root / "experiments/EXP-21"
            base.mkdir(parents=True, exist_ok=True)
            (base / "binding.json").write_text(
                json.dumps(
                    {
                        "kind": "experiment_identity",
                        "experiment_id": "EXP-21",
                        "initialization": {
                            "branch_ref": "refs/heads/exp/21",
                            "final_tag_ref": "refs/tags/exp-final/21",
                        },
                    }
                ),
                encoding="utf-8",
            )
            ctx = resolve_trusted_archive(
                "owner/repo",
                self._archive_payload("archive.prepare", mode="ATOMIC_DELETE"),
                root,
                authority="request",
            )
        self.assertEqual(ctx.action, "PREPARE")
        self.assertEqual(ctx.expected_branch_sha, "b" * 40)
        self.assertEqual(ctx.mode, "ATOMIC_DELETE")

    @patch("trusted_writer.github_json")
    @patch("trusted_writer.github_json_optional")
    def test_archive_atomic_delete_observe_classifies_ref_committed(self, optional, api):
        import tempfile

        optional.side_effect = [
            None,
            {"object": {"type": "tag", "sha": "1" * 40}},
        ]
        api.return_value = {
            "object": {"type": "commit", "sha": "b" * 40},
            "message": "\n".join(
                [
                    "game-exp-experiment: EXP-21",
                    "game-exp-archive-id: A-21-1",
                    "game-exp-mode: ATOMIC_DELETE",
                    "game-exp-source-sha: " + "b" * 40,
                ]
            ),
        }
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_archive_fixture(root)
            ctx = resolve_trusted_archive(
                "owner/repo",
                self._archive_payload("archive.observe", archive_id="A-21-1"),
                root,
                authority="archive",
            )
        self.assertEqual(ctx.ref_status, "REF_COMMITTED")
        self.assertIsNone(ctx.observed_branch_sha)
        self.assertEqual(ctx.observed_final_tag_target, "b" * 40)

    @patch("trusted_writer.github_json_optional")
    def test_archive_observe_classifies_not_executed(self, optional):
        import tempfile

        optional.side_effect = [
            {"object": {"type": "commit", "sha": "b" * 40}},
            None,
        ]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_archive_fixture(root)
            ctx = resolve_trusted_archive(
                "owner/repo",
                self._archive_payload("archive.observe", archive_id="A-21-1"),
                root,
                authority="archive",
            )
        self.assertEqual(ctx.ref_status, "NOT_EXECUTED")
        self.assertEqual(ctx.observed_branch_sha, "b" * 40)
        self.assertIsNone(ctx.observed_final_tag_target)

    @patch("trusted_writer.github_json")
    @patch("trusted_writer.github_json_optional")
    def test_archive_retain_observe_requires_tag_and_exact_branch(self, optional, api):
        import tempfile

        optional.side_effect = [
            {"object": {"type": "commit", "sha": "b" * 40}},
            {"object": {"type": "tag", "sha": "1" * 40}},
        ]
        api.return_value = {
            "object": {"type": "commit", "sha": "b" * 40},
            "message": "\n".join(
                [
                    "game-exp-experiment: EXP-21",
                    "game-exp-archive-id: A-21-1",
                    "game-exp-mode: RETAIN_BRANCH",
                    "game-exp-source-sha: " + "b" * 40,
                ]
            ),
        }
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_archive_fixture(root, mode="RETAIN_BRANCH")
            ctx = resolve_trusted_archive(
                "owner/repo",
                self._archive_payload("archive.observe", archive_id="A-21-1"),
                root,
                authority="archive",
            )
        self.assertEqual(ctx.ref_status, "REF_COMMITTED")
        self.assertEqual(ctx.observed_branch_sha, "b" * 40)

    @patch("trusted_writer.github_json")
    @patch("trusted_writer.github_json_optional")
    def test_archive_observe_classifies_branch_drift_as_conflict(self, optional, api):
        import tempfile

        optional.side_effect = [
            {"object": {"type": "commit", "sha": "c" * 40}},
            {"object": {"type": "tag", "sha": "1" * 40}},
        ]
        api.return_value = {
            "object": {"type": "commit", "sha": "b" * 40},
            "message": "\n".join(
                [
                    "game-exp-experiment: EXP-21",
                    "game-exp-archive-id: A-21-1",
                    "game-exp-mode: RETAIN_BRANCH",
                    "game-exp-source-sha: " + "b" * 40,
                ]
            ),
        }
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_archive_fixture(root, mode="RETAIN_BRANCH")
            ctx = resolve_trusted_archive(
                "owner/repo",
                self._archive_payload("archive.observe", archive_id="A-21-1"),
                root,
                authority="archive",
            )
        self.assertEqual(ctx.ref_status, "REF_CONFLICT")
        self.assertEqual(ctx.observed_branch_sha, "c" * 40)

    @patch.dict("os.environ", {"GAME_EXP_ACTOR_LOGIN": "siskosun"}, clear=False)
    @patch("trusted_writer.github_json")
    def test_decision_actor_resolves_from_github_permission(self, api):
        api.return_value = {
            "permission": "admin",
            "user": {"login": "siskosun", "id": 202578583},
        }
        ctx = resolve_trusted_actor(
            "siskosun/toy2game",
            {"kind": "operation_request", "operation": "experiment.decision"},
        )
        self.assertEqual(ctx.login, "siskosun")
        self.assertEqual(ctx.user_id, "202578583")
        self.assertEqual(ctx.permission, "admin")

    @patch.dict("os.environ", {"GAME_EXP_ACTOR_LOGIN": "siskosun"}, clear=False)
    @patch("trusted_writer.github_json")
    def test_review_actor_uses_same_trusted_permission_resolver(self, api):
        api.return_value = {
            "permission": "write",
            "user": {"login": "siskosun", "id": 202578583},
        }
        ctx = resolve_trusted_actor(
            "siskosun/toy2game",
            {"kind": "operation_request", "operation": "review.record"},
        )
        self.assertEqual(ctx.login, "siskosun")
        self.assertEqual(ctx.permission, "write")

    @patch.dict("os.environ", {}, clear=True)
    def test_decision_actor_requires_trusted_github_login(self):
        with self.assertRaises(DomainError) as ctx:
            resolve_trusted_actor(
                "siskosun/toy2game",
                {"kind": "operation_request", "operation": "experiment.decision"},
            )
        self.assertEqual(ctx.exception.code, "DOMAIN_AUTHORIZATION_FAILED")

    @patch.dict("os.environ", {"GAME_EXP_ACTOR_LOGIN": "siskosun"}, clear=False)
    @patch("trusted_writer.github_json")
    def test_decision_actor_identity_mismatch_rejected(self, api):
        api.return_value = {
            "permission": "admin",
            "user": {"login": "other", "id": 1},
        }
        with self.assertRaises(DomainError) as ctx:
            resolve_trusted_actor(
                "siskosun/toy2game",
                {"kind": "operation_request", "operation": "experiment.decision"},
            )
        self.assertEqual(ctx.exception.code, "DOMAIN_AUTHORIZATION_FAILED")

    def test_non_binding_request_needs_no_github_resolution(self):
        self.assertIsNone(
            resolve_trusted_binding(
                "siskosun/toy2game",
                {"kind": "operation_request", "operation": "transport.probe"},
            )
        )


if __name__ == "__main__":
    unittest.main()
