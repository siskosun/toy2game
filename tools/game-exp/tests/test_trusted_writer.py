from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))

from domain_core import DomainError  # noqa: E402
from trusted_writer import resolve_trusted_actor, resolve_trusted_binding, resolve_trusted_candidate  # noqa: E402


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
