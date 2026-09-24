from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))

from rehearsal_control import (  # noqa: E402
    RehearsalControlError,
    integration_tree,
    payload,
    prepare,
    rehearsal_policy_digest,
)
from protocol_core import canonical_json_bytes, decode_payload_b64, digest_object  # noqa: E402


class RehearsalControlTests(unittest.TestCase):
    @patch("rehearsal_control.github_json")
    @patch("rehearsal_control.github_content_json")
    def test_prepare_uses_ledger_candidate_scope_and_current_main(self, content, api):
        manifest = {
            "scope": {"allowed": ["games/**"], "avoid": [".github/**"]}
        }
        manifest_digest = digest_object(manifest)
        content.side_effect = [
            {
                "kind": "experiment_state",
                "experiment_id": "EXP-21",
                "lifecycle": "PROMISING",
                "current_candidate_id": "C-21-123-1",
            },
            {
                "kind": "candidate",
                "experiment_id": "EXP-21",
                "candidate_id": "C-21-123-1",
                "source_sha": "b" * 40,
                "manifest_digest": manifest_digest,
            },
            {
                "kind": "experiment_identity",
                "experiment_id": "EXP-21",
                "parent_sha": "d" * 40,
                "initialization": {
                    "manifest_digest": manifest_digest,
                    "manifest_path": "experiments/EXP-21/manifest.yaml",
                },
            },
            manifest,
        ]
        api.return_value = {"object": {"sha": "a" * 40}}
        args = argparse.Namespace(
            repo="owner/repo",
            experiment_id="EXP-21",
            run_id="456",
            run_attempt="1",
            workflow_source_sha="c" * 40,
        )
        result = prepare(args)
        self.assertEqual(result["candidate_id"], "C-21-123-1")
        self.assertEqual(result["source_sha"], "b" * 40)
        self.assertEqual(result["base_sha"], "d" * 40)
        self.assertEqual(result["main_sha"], "a" * 40)
        self.assertEqual(result["rehearsal_id"], "R-21-456-1")
        self.assertEqual(
            result["rehearsal_ref"],
            "refs/tags/exp-rehearsal/21/R-21-456-1",
        )
        scope = json.loads(base64.b64decode(result["scope_b64"]).decode("utf-8"))
        self.assertEqual(scope["allowed"], ["games/**"])
        self.assertEqual(scope["avoid"], [".github/**"])
        self.assertEqual(
            scope["metadata_paths"],
            ["experiments/EXP-21/manifest.yaml"],
        )
        self.assertEqual(result["policy_digest"], rehearsal_policy_digest())

    @patch("rehearsal_control.github_json")
    @patch("rehearsal_control.github_content_json")
    def test_prepare_rejects_non_promising_experiment(self, content, api):
        content.side_effect = [
            {
                "kind": "experiment_state",
                "experiment_id": "EXP-21",
                "lifecycle": "REVIEW",
                "current_candidate_id": "C-21-123-1",
            }
        ]
        args = argparse.Namespace(
            repo="owner/repo",
            experiment_id="EXP-21",
            run_id="456",
            run_attempt="1",
            workflow_source_sha="c" * 40,
        )
        with self.assertRaisesRegex(RehearsalControlError, "PROMISING"):
            prepare(args)
        api.assert_not_called()

    def test_payload_binds_exact_rehearsal_context(self):
        with tempfile.TemporaryDirectory() as td:
            context_path = str(Path(td) / "context.json")
            args = argparse.Namespace(
                experiment_id="EXP-21",
                candidate_id="C-21-123-1",
                rehearsal_id="R-21-456-1",
                main_sha="a" * 40,
                source_sha="b" * 40,
                integration_sha="c" * 40,
                integration_tree_sha="d" * 40,
                rehearsal_ref="refs/tags/exp-rehearsal/21/R-21-456-1",
                workflow_source_sha="e" * 40,
                run_id="456",
                run_attempt="1",
                policy_digest="sha256:" + "f" * 64,
                context_output=context_path,
            )
            result = payload(args)
            request = decode_payload_b64(result["payload_b64"])
            context = json.loads(Path(context_path).read_text(encoding="utf-8"))
        self.assertEqual(result["request_id"], "req-rehearsal-456-1")
        self.assertEqual(request["operation"], "rehearsal.register")
        self.assertEqual(request["input"], {"experiment_id": "EXP-21"})
        self.assertEqual(
            request["preconditions"],
            {"candidate_id": "C-21-123-1", "main_sha": "a" * 40},
        )
        self.assertEqual(context["integration_sha"], "c" * 40)
        self.assertEqual(context["integration_tree_sha"], "d" * 40)
        self.assertEqual(
            {row["name"] for row in context["checks"]},
            {
                "scope",
                "merge",
                "project_tests",
                "build",
                "trusted_tree_recompute",
                "main_freshness",
            },
        )


    def _git(self, repo: Path, *args: str) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        return proc.stdout.strip()

    def _repo_fixture(self, extra_source: dict[str, str] | None = None):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        repo = Path(td.name)
        self._git(repo, "init")
        self._git(repo, "config", "user.name", "test")
        self._git(repo, "config", "user.email", "test@example.com")
        (repo / "games").mkdir()
        (repo / "games/a.txt").write_text("base\n", encoding="utf-8")
        (repo / "README.md").write_text("base-readme\n", encoding="utf-8")
        self._git(repo, "add", ".")
        self._git(repo, "commit", "-m", "base")
        base_sha = self._git(repo, "rev-parse", "HEAD")

        self._git(repo, "checkout", "-b", "source")
        (repo / "games/a.txt").write_text("candidate\n", encoding="utf-8")
        meta = repo / "experiments/EXP-21"
        meta.mkdir(parents=True)
        (meta / "manifest.yaml").write_text("schema_version: 1\n", encoding="utf-8")
        for path, value in (extra_source or {}).items():
            target = repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(value, encoding="utf-8")
        self._git(repo, "add", ".")
        self._git(repo, "commit", "-m", "candidate")
        source_sha = self._git(repo, "rev-parse", "HEAD")

        self._git(repo, "checkout", "-b", "main-test", base_sha)
        (repo / "README.md").write_text("latest-main\n", encoding="utf-8")
        self._git(repo, "add", "README.md")
        self._git(repo, "commit", "-m", "main")
        main_sha = self._git(repo, "rev-parse", "HEAD")
        scope = {
            "allowed": ["games/**"],
            "avoid": [".github/**"],
            "metadata_paths": ["experiments/EXP-21/manifest.yaml"],
        }
        scope_b64 = base64.b64encode(canonical_json_bytes(scope)).decode("ascii")
        return repo, base_sha, source_sha, main_sha, scope_b64

    def test_scope_filtered_tree_keeps_main_and_only_allowed_candidate_changes(self):
        repo, base_sha, source_sha, main_sha, scope_b64 = self._repo_fixture()
        result = integration_tree(
            argparse.Namespace(
                repo_dir=str(repo),
                base_sha=base_sha,
                source_sha=source_sha,
                main_sha=main_sha,
                scope_b64=scope_b64,
            )
        )
        self.assertEqual(result["included_paths"], ["games/a.txt"])
        self.assertEqual(
            result["ignored_metadata_paths"],
            ["experiments/EXP-21/manifest.yaml"],
        )
        self.assertEqual((repo / "games/a.txt").read_text(encoding="utf-8"), "candidate\n")
        self.assertEqual((repo / "README.md").read_text(encoding="utf-8"), "latest-main\n")
        self.assertFalse((repo / "experiments/EXP-21/manifest.yaml").exists())
        self.assertEqual(result["integration_tree_sha"], self._git(repo, "write-tree"))

    def test_scope_filter_rejects_avoid_path(self):
        repo, base_sha, source_sha, main_sha, scope_b64 = self._repo_fixture(
            {".github/workflows/evil.yml": "name: evil\n"}
        )
        with self.assertRaisesRegex(RehearsalControlError, "explicitly avoided"):
            integration_tree(
                argparse.Namespace(
                    repo_dir=str(repo),
                    base_sha=base_sha,
                    source_sha=source_sha,
                    main_sha=main_sha,
                    scope_b64=scope_b64,
                )
            )

    def test_scope_filter_rejects_other_out_of_scope_source_change(self):
        repo, base_sha, source_sha, main_sha, scope_b64 = self._repo_fixture(
            {"docs/outside.txt": "outside\n"}
        )
        with self.assertRaisesRegex(RehearsalControlError, "out-of-scope"):
            integration_tree(
                argparse.Namespace(
                    repo_dir=str(repo),
                    base_sha=base_sha,
                    source_sha=source_sha,
                    main_sha=main_sha,
                    scope_b64=scope_b64,
                )
            )


if __name__ == "__main__":
    unittest.main()
