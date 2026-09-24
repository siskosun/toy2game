from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))

from domain_core import DomainError, candidate_policy_digest  # noqa: E402
from trusted_writer import resolve_trusted_binding, resolve_trusted_candidate  # noqa: E402


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


def candidate_payload():
    experiment_id = "EXP-42"
    candidate_id = "C-42-123-1"
    source_sha = "c" * 40
    workflow_sha = "b" * 40
    artifact_digest = "sha256:" + "1" * 64
    manifest_digest = "sha256:" + "2" * 64
    policy_digest = candidate_policy_digest()
    release_tag = "game-exp-candidate-123-1"
    return {
        "kind": "operation_request",
        "schema_version": 1,
        "operation": "candidate.attest",
        "input": {
            "experiment_id": experiment_id,
            "candidate_id": candidate_id,
            "source_sha": source_sha,
            "source_anchor_ref": f"refs/tags/exp-candidate/42/{candidate_id}",
            "manifest_digest": manifest_digest,
            "artifact_digest": artifact_digest,
            "artifact_level": 3,
            "workflow_source_sha": workflow_sha,
            "run_id": "123",
            "run_attempt": "1",
            "policy_digest": policy_digest,
            "checks": [],
            "retention": {
                "provider": "github-immutable-release",
                "release_tag": release_tag,
                "asset_name": "candidate.tgz",
            },
        },
        "preconditions": {},
    }


def candidate_api_evidence(value=None):
    value = value or candidate_payload()
    i = value["input"]
    message = "\n".join(
        [
            f"game-exp-experiment: {i['experiment_id']}",
            f"game-exp-candidate-id: {i['candidate_id']}",
            f"game-exp-source-sha: {i['source_sha']}",
            f"game-exp-artifact-digest: {i['artifact_digest']}",
            f"game-exp-manifest-digest: {i['manifest_digest']}",
            f"game-exp-workflow-source-sha: {i['workflow_source_sha']}",
            f"game-exp-run-id: {i['run_id']}",
            f"game-exp-run-attempt: {i['run_attempt']}",
            f"game-exp-policy-digest: {i['policy_digest']}",
            f"game-exp-release-tag: {i['retention']['release_tag']}",
        ]
    )
    return [
        {"object": {"type": "tag", "sha": "d" * 40}},
        {
            "object": {"type": "commit", "sha": i["source_sha"]},
            "message": message,
        },
        {
            "immutable": True,
            "target_commitish": i["source_sha"],
            "assets": [
                {
                    "name": "candidate.tgz",
                    "digest": i["artifact_digest"],
                    "size": 12345,
                }
            ],
        },
        {
            "run_attempt": 1,
            "event": "workflow_dispatch",
            "path": ".github/workflows/game-exp-candidate.yml",
            "head_sha": i["workflow_source_sha"],
            "status": "in_progress",
            "conclusion": None,
        },
    ]


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

    @patch("trusted_writer.github_json")
    def test_candidate_resolver_rechecks_anchor_release_and_run(self, api):
        value = candidate_payload()
        api.side_effect = candidate_api_evidence(value)
        ctx = resolve_trusted_candidate("siskosun/toy2game", value)
        self.assertEqual(ctx.experiment_id, "EXP-42")
        self.assertEqual(ctx.candidate_id, "C-42-123-1")
        self.assertEqual(ctx.source_sha, "c" * 40)
        self.assertEqual(ctx.release_tag, "game-exp-candidate-123-1")
        self.assertEqual(api.call_count, 4)

    @patch("trusted_writer.github_json")
    def test_candidate_resolver_rejects_release_asset_digest_mismatch(self, api):
        value = candidate_payload()
        evidence = candidate_api_evidence(value)
        evidence[2]["assets"][0]["digest"] = "sha256:" + "f" * 64
        api.side_effect = evidence
        with self.assertRaisesRegex(DomainError, "asset digest"):
            resolve_trusted_candidate("siskosun/toy2game", value)

    @patch("trusted_writer.github_json")
    def test_candidate_resolver_rejects_anchor_metadata_mismatch(self, api):
        value = candidate_payload()
        evidence = candidate_api_evidence(value)
        evidence[1]["message"] = evidence[1]["message"].replace(
            "game-exp-run-attempt: 1",
            "game-exp-run-attempt: 2",
        )
        api.side_effect = evidence
        with self.assertRaisesRegex(DomainError, "metadata mismatch"):
            resolve_trusted_candidate("siskosun/toy2game", value)

    @patch("trusted_writer.github_json")
    def test_candidate_resolver_rejects_wrong_workflow_path(self, api):
        value = candidate_payload()
        evidence = candidate_api_evidence(value)
        evidence[3]["path"] = ".github/workflows/not-trusted.yml"
        api.side_effect = evidence
        with self.assertRaisesRegex(DomainError, "trusted candidate workflow"):
            resolve_trusted_candidate("siskosun/toy2game", value)

    @patch("trusted_writer.github_json")
    def test_candidate_resolver_rejects_historical_completed_run(self, api):
        value = candidate_payload()
        evidence = candidate_api_evidence(value)
        evidence[3]["status"] = "completed"
        evidence[3]["conclusion"] = "success"
        api.side_effect = evidence
        with self.assertRaisesRegex(DomainError, "must finalize while"):
            resolve_trusted_candidate("siskosun/toy2game", value)

    def test_non_binding_request_needs_no_github_resolution(self):
        self.assertIsNone(
            resolve_trusted_binding(
                "siskosun/toy2game",
                {"kind": "operation_request", "operation": "transport.probe"},
            )
        )


if __name__ == "__main__":
    unittest.main()
