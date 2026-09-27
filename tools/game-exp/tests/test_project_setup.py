from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))

import project_setup  # noqa: E402


def completed(args=None, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args or ["gh"], returncode, stdout, stderr)


class ProjectSetupTests(unittest.TestCase):

    def test_gh_api_repo_root_has_no_trailing_slash(self):
        with patch("project_setup._run") as run:
            run.return_value = completed(stdout="{}")
            project_setup._gh_api("owner/repo", "")
        self.assertEqual(run.call_args.args[0][2], "repos/owner/repo")

    def test_gh_api_nested_endpoint_is_joined_once(self):
        with patch("project_setup._run") as run:
            run.return_value = completed(stdout="[]")
            project_setup._gh_api(
                "owner/repo",
                "/rulesets?per_page=100",
                check=False,
            )
        self.assertEqual(
            run.call_args.args[0][2],
            "repos/owner/repo/rulesets?per_page=100",
        )

    def test_gh_api_uses_supported_api_version(self):
        with patch("project_setup._run") as run:
            run.return_value = completed(stdout="{}")
            project_setup._gh_api("owner/repo", "")
        self.assertIn(
            "X-GitHub-Api-Version: 2022-11-28",
            run.call_args.args[0],
        )

    def test_preflight_blocks_private_free_ruleset_gap_before_provision(self):
        metadata = {
            "private": True,
            "visibility": "private",
            "default_branch": "main",
            "owner": {"type": "User"},
            "permissions": {"admin": True},
        }
        with (
            patch("project_setup._repo_metadata", return_value=metadata),
            patch(
                "project_setup._ruleset_probe",
                return_value={
                    "status": "BLOCKED_PLAN",
                    "available": False,
                    "code": "RULESETS_PLAN_UNSUPPORTED",
                    "resolution_choices": [
                        "make_repository_public",
                        "upgrade_github_plan",
                    ],
                },
            ),
            patch("project_setup._gh_api", return_value=completed()),
        ):
            result = project_setup.preflight("owner/repo")
        self.assertEqual(result["status"], "BLOCKED_PLAN")
        self.assertFalse(result["ready_to_provision"])
        self.assertEqual(
            result["blockers"][0]["code"],
            "RULESETS_PLAN_UNSUPPORTED",
        )

    def test_preflight_requires_admin_and_committed_workflow(self):
        metadata = {
            "private": False,
            "visibility": "public",
            "default_branch": "main",
            "owner": {"type": "User"},
            "permissions": {"admin": False, "push": True},
        }
        with (
            patch("project_setup._repo_metadata", return_value=metadata),
            patch(
                "project_setup._ruleset_probe",
                return_value={"status": "PASS", "available": True, "rulesets": []},
            ),
            patch(
                "project_setup._gh_api",
                return_value=completed(returncode=1, stderr="404"),
            ),
        ):
            result = project_setup.preflight("owner/repo")
        self.assertEqual(result["status"], "BLOCKED_PERMISSION")
        codes = {row["code"] for row in result["blockers"]}
        self.assertEqual(
            codes,
            {"ADMIN_REQUIRED", "GAME_EXP_WORKFLOWS_NOT_COMMITTED"},
        )

    def test_ledger_initialization_is_idempotent(self):
        calls = []

        def fake_api(repo, suffix, **kwargs):
            calls.append((suffix, kwargs.get("method", "GET"), kwargs.get("body")))
            if suffix == "git/blobs":
                return completed(stdout='{"sha":"%s"}' % ("1" * 40))
            if suffix == "git/trees":
                return completed(stdout='{"sha":"%s"}' % ("2" * 40))
            if suffix == "git/commits":
                return completed(stdout='{"sha":"%s"}' % ("3" * 40))
            if suffix == "git/refs":
                return completed(stdout='{}')
            raise AssertionError(suffix)

        with (
            patch("project_setup._ledger_head", side_effect=[None, "3" * 40]),
            patch("project_setup._gh_api", side_effect=fake_api),
        ):
            result = project_setup._ensure_ledger("owner/repo")
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["changed"])
        self.assertEqual(result["ledger_head"], "3" * 40)
        self.assertIn(("git/refs", "POST", {"ref": "refs/heads/game-exp/ledger", "sha": "3" * 40}), calls)

        with patch("project_setup._ledger_head", return_value="4" * 40):
            replay = project_setup._ensure_ledger("owner/repo")
        self.assertFalse(replay["changed"])
        self.assertEqual(replay["ledger_head"], "4" * 40)

    def test_writer_credentials_preserve_healthy_key_and_secret(self):
        key = {
            "id": 9,
            "title": project_setup.WRITER_KEY_TITLE,
            "read_only": False,
        }
        with (
            patch("project_setup._deploy_keys", return_value=[key]),
            patch(
                "project_setup._secret_names",
                return_value={project_setup.WRITER_SECRET},
            ),
            patch("project_setup._generate_writer_keypair") as generate,
        ):
            result = project_setup._ensure_writer_credentials("owner/repo")
        self.assertFalse(result["changed"])
        generate.assert_not_called()

    def test_writer_credentials_refuse_other_write_deploy_keys(self):
        with (
            patch(
                "project_setup._deploy_keys",
                return_value=[{"id": 3, "title": "other", "read_only": False}],
            ),
            patch("project_setup._secret_names", return_value=set()),
        ):
            with self.assertRaises(project_setup.ProjectSetupError):
                project_setup._ensure_writer_credentials("owner/repo")

    def test_rulesets_existing_mismatch_fails_closed(self):
        existing = [{"id": 11, "name": "game-exp ledger", "enforcement": "active"}]
        with (
            patch(
                "project_setup._ruleset_probe",
                return_value={"status": "PASS", "available": True, "rulesets": existing},
            ),
            patch(
                "project_setup._gh_api",
                return_value=completed(
                    stdout='{"id":11,"name":"game-exp ledger","target":"branch",'
                    '"enforcement":"active","bypass_actors":[],"conditions":{},'
                    '"rules":[]}'
                ),
            ),
        ):
            with self.assertRaises(project_setup.ProjectSetupError):
                project_setup._ensure_rulesets("owner/repo")

    def test_provision_stops_before_mutation_when_preflight_blocks(self):
        blocked = {
            "status": "BLOCKED_PLAN",
            "repo": "owner/repo",
            "ready_to_provision": False,
        }
        with (
            patch("project_setup.preflight", return_value=blocked),
            patch("project_setup._ensure_ledger") as ledger,
        ):
            result = project_setup.provision("owner/repo")
        self.assertEqual(result["status"], "BLOCKED_PLAN")
        self.assertFalse(result["complete"])
        ledger.assert_not_called()

    def test_provision_requires_final_doctor_pass(self):
        with (
            patch(
                "project_setup.preflight",
                return_value={
                    "status": "PASS",
                    "repo": "owner/repo",
                    "ready_to_provision": True,
                },
            ),
            patch("project_setup._ensure_ledger", return_value={"status": "PASS"}),
            patch(
                "project_setup._ensure_writer_credentials",
                return_value={"status": "PASS"},
            ),
            patch(
                "project_setup._ensure_immutable_releases",
                return_value={"status": "PASS"},
            ),
            patch(
                "project_setup._ensure_repository_baseline",
                return_value={"status": "PASS"},
            ),
            patch(
                "project_setup._ensure_rulesets",
                return_value={"status": "PASS"},
            ),
            patch(
                "project_setup._run_selftest",
                return_value={"status": "PASS", "run_id": 1},
            ),
            patch("project_setup.GitHubTransport"),
            patch("project_setup.GameExpClient") as client_cls,
        ):
            client_cls.return_value.doctor.return_value = {
                "status": "FAIL",
                "checks": [{"name": "rulesets", "status": "FAIL"}],
            }
            result = project_setup.provision("owner/repo")
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["complete"])


if __name__ == "__main__":
    unittest.main()
