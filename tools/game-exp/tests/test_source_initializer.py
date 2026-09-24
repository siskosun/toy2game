from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))

from protocol_core import build_operation_payload, digest_object  # noqa: E402
from source_initializer import (  # noqa: E402
    InitError,
    initialize_source,
    load_authoritative_binding,
    manifest_yaml_bytes,
)


def git(args, cwd=None):
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return proc.stdout.strip()


def make_manifest(issue="42", parent="a" * 40, request_id="req_bind_42"):
    return {
        "created_at": "2026-09-24T13:50:00+08:00",
        "experiment": {
            "host": "github.com",
            "issue_id": "5000000042",
            "issue_number": issue,
            "repository_id": "1384446218",
        },
        "hypothesis": "Initialization is recoverable.",
        "kill_criteria": ["Initialization cannot be independently reconstructed."],
        "operation_id": request_id,
        "parent": {"commit": parent, "experiment": None},
        "review": {"protocol": "blind-playtest-v1"},
        "runtime": {
            "addons_lock": "sha256:none",
            "export_templates": "n/a",
            "godot": "n/a",
        },
        "schema_version": 1,
        "scope": {"allowed": ["games/**"], "avoid": [".github/**"]},
        "success_criteria": ["Branch and base tag can be verified after retry."],
        "title": "Initializer test",
    }


def make_binding(manifest, request_id="req_bind_42"):
    issue = manifest["experiment"]["issue_number"]
    experiment_id = f"EXP-{issue}"
    init = {
        "base_tag_ref": f"refs/tags/exp-base/{issue}",
        "branch_ref": f"refs/heads/exp/{issue}",
        "final_tag_ref": f"refs/tags/exp-final/{issue}",
        "manifest_digest": digest_object(manifest),
        "manifest_path": f"experiments/{experiment_id}/manifest.yaml",
        "manifest_schema_version": 1,
        "manifest_snapshot_path": f"experiments/{experiment_id}/manifest.json",
    }
    init["initialization_plan_digest"] = digest_object(init)
    payload = build_operation_payload("experiment.bind", {"manifest": manifest})
    return {
        "canonical": {
            "host": "github.com",
            "issue_id": manifest["experiment"]["issue_id"],
            "issue_number": issue,
            "repository_id": manifest["experiment"]["repository_id"],
        },
        "experiment_id": experiment_id,
        "initialization": init,
        "inputs_digest": digest_object(payload),
        "kind": "experiment_identity",
        "parent_sha": manifest["parent"]["commit"],
        "request_id": request_id,
    }


def make_operation(manifest, request_id="req_bind_42"):
    payload = build_operation_payload("experiment.bind", {"manifest": manifest})
    experiment_id = f"EXP-{manifest['experiment']['issue_number']}"
    root = f"experiments/{experiment_id}"
    return {
        "domain_experiment_id": experiment_id,
        "domain_paths": sorted(
            [f"{root}/binding.json", f"{root}/manifest.json", f"{root}/state.json"]
        ),
        "domain_status": "APPLIED",
        "payload": payload,
        "payload_digest": digest_object(payload),
        "request_id": request_id,
    }


class SourceInitializerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.remote = self.root / "remote.git"
        self.seed = self.root / "seed"

        git(["init", "--bare", str(self.remote)])
        git(["init", str(self.seed)])
        git(["config", "user.name", "test"], cwd=self.seed)
        git(["config", "user.email", "test@example.com"], cwd=self.seed)
        (self.seed / "README.md").write_text("seed\n", encoding="utf-8")
        git(["add", "README.md"], cwd=self.seed)
        git(["commit", "-m", "seed"], cwd=self.seed)
        self.parent = git(["rev-parse", "HEAD"], cwd=self.seed)
        git(["remote", "add", "origin", str(self.remote)], cwd=self.seed)
        git(["push", "origin", "HEAD:main"], cwd=self.seed)

        self.manifest = make_manifest(parent=self.parent)
        self.binding = make_binding(self.manifest)
        self.state = {
            "archive_lock": None,
            "created_by_request_id": "req_bind_42",
            "experiment_id": "EXP-42",
            "kind": "experiment_state",
            "last_decision_id": None,
            "lifecycle": "ACTIVE",
            "sequence": 0,
        }

    def initialize(self):
        return initialize_source(
            remote_url=str(self.remote),
            experiment_id="EXP-42",
            binding=self.binding,
            manifest=self.manifest,
            state=self.state,
        )

    def test_create_branch_and_annotated_base_tag_atomically(self):
        result = self.initialize()
        self.assertEqual(result["status"], "INITIALIZED")
        self.assertFalse(result["replayed"])

        branch = git(["--git-dir", str(self.remote), "rev-parse", "refs/heads/exp/42"])
        tag_type = git(["--git-dir", str(self.remote), "cat-file", "-t", "refs/tags/exp-base/42"])
        peeled = git(["--git-dir", str(self.remote), "rev-parse", "refs/tags/exp-base/42^{}"])
        self.assertEqual(branch, result["initialization_commit"])
        self.assertEqual(tag_type, "tag")
        self.assertEqual(peeled, self.parent)

        inspect = self.root / "inspect"
        git(["init", str(inspect)])
        git(["remote", "add", "origin", str(self.remote)], cwd=inspect)
        git(
            [
                "fetch",
                "--no-tags",
                "origin",
                "refs/heads/exp/42:refs/remotes/origin/exp/42",
            ],
            cwd=inspect,
        )
        actual = subprocess.run(
            ["git", "show", f"{branch}:experiments/EXP-42/manifest.yaml"],
            cwd=inspect,
            stdout=subprocess.PIPE,
            check=True,
        ).stdout
        self.assertEqual(actual, manifest_yaml_bytes(self.manifest))

    def test_retry_after_branch_advances_is_idempotent(self):
        first = self.initialize()

        work = self.root / "advance"
        git(["clone", str(self.remote), str(work)])
        git(["fetch", "origin", "refs/heads/exp/42:refs/remotes/origin/exp/42"], cwd=work)
        git(["checkout", "-b", "exp-42", "refs/remotes/origin/exp/42"], cwd=work)
        git(["config", "user.name", "developer"], cwd=work)
        git(["config", "user.email", "developer@example.com"], cwd=work)
        (work / "games").mkdir()
        (work / "games" / "probe.txt").write_text("advance\n", encoding="utf-8")
        git(["add", "games/probe.txt"], cwd=work)
        git(["commit", "-m", "advance experiment"], cwd=work)
        advanced = git(["rev-parse", "HEAD"], cwd=work)
        git(["push", "origin", "HEAD:refs/heads/exp/42"], cwd=work)

        second = self.initialize()
        self.assertTrue(second["replayed"])
        self.assertEqual(second["initialization_commit"], first["initialization_commit"])
        self.assertEqual(second["branch_head"], advanced)

    def test_partial_remote_refs_are_conflict(self):
        self.initialize()
        git(["--git-dir", str(self.remote), "update-ref", "-d", "refs/tags/exp-base/42"])
        with self.assertRaisesRegex(InitError, "partial source initialization"):
            self.initialize()

    def test_inactive_state_cannot_initialize(self):
        self.state["lifecycle"] = "REVIEW"
        with self.assertRaises(InitError) as ctx:
            self.initialize()
        self.assertEqual(ctx.exception.code, "INITIALIZATION_INVALID")

    @patch("source_initializer._ledger_json")
    def test_corrupt_bound_operation_digest_is_rejected(self, ledger):
        manifest = make_manifest(parent=self.parent)
        binding = make_binding(manifest)
        state = dict(self.state)
        operation = make_operation(manifest)
        operation["payload"]["input"]["manifest"]["scope"]["allowed"].append(".github/**")
        ledger.side_effect = [binding, manifest, state, operation]

        with self.assertRaisesRegex(InitError, "payload digest mismatch"):
            load_authoritative_binding("owner/repo", "EXP-42")

    def test_manifest_yaml_is_deterministic_and_does_not_mutate_input(self):
        before = json.loads(json.dumps(self.manifest))
        a = manifest_yaml_bytes(self.manifest)
        b = manifest_yaml_bytes(self.manifest)
        self.assertEqual(a, b)
        self.assertEqual(self.manifest, before)
        self.assertIn(b'allowed:\n    - "games/**"\n', a)


if __name__ == "__main__":
    unittest.main()
