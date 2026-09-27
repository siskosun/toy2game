from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]
WORKFLOWS = {
    "candidate": ROOT / ".github/workflows/game-exp-candidate.yml",
    "rehearsal": ROOT / ".github/workflows/game-exp-rehearsal.yml",
    "archive_snapshot": ROOT / ".github/workflows/game-exp-archive-snapshot-verify.yml",
}

ASYNC_WORKFLOWS = {
    "initialize": ROOT / ".github/workflows/game-exp-source-initializer.yml",
    "candidate_build": ROOT / ".github/workflows/game-exp-candidate.yml",
    "rehearse": ROOT / ".github/workflows/game-exp-rehearsal.yml",
    "integrate": ROOT / ".github/workflows/game-exp-integration.yml",
    "integrate_finalize": ROOT / ".github/workflows/game-exp-integration-finalize.yml",
    "archive": ROOT / ".github/workflows/game-exp-archive.yml",
}


class ProjectPolicyWorkflowContractTests(unittest.TestCase):
    def test_lifecycle_workflows_use_trusted_project_policy(self):
        for name, path in WORKFLOWS.items():
            text = path.read_text(encoding="utf-8")
            with self.subTest(workflow=name):
                self.assertIn("github.workflow_sha", text)
                self.assertIn("tools/game-exp/project_policy.py", text)
                self.assertIn(".game-exp/project-policy.json", text)

    def test_project_commands_are_not_hardcoded_in_lifecycle_workflows(self):
        forbidden = re.compile(r"(?m)^\s*run:\s*npm\s+(?:ci|test|run\s+build)\s*$")
        for name, path in WORKFLOWS.items():
            text = path.read_text(encoding="utf-8")
            with self.subTest(workflow=name):
                self.assertIsNone(forbidden.search(text))

    def test_candidate_artifact_shape_is_policy_driven(self):
        text = WORKFLOWS["candidate"].read_text(encoding="utf-8")
        self.assertNotIn("test -f dist/index.html", text)
        self.assertNotIn("grep -q '^dist/", text)
        self.assertIn("policy_digest(policy)", text)

    def test_async_workflows_require_trusted_request_id_guard(self):
        for action, path in ASYNC_WORKFLOWS.items():
            text = path.read_text(encoding="utf-8")
            with self.subTest(action=action):
                self.assertIn("request_id:", text)
                self.assertIn(f"run-name: game-exp:{action}:", text)
                self.assertIn("workflow_guard.py", text)
                self.assertIn("--request-id", text)
                self.assertIn("--experiment-id", text)
                self.assertIn("needs: dedupe", text)
                self.assertIn("cancel-in-progress: false", text)

    def test_rehearsal_policy_import_is_explicitly_routed(self):
        text = WORKFLOWS["rehearsal"].read_text(encoding="utf-8")
        self.assertIn(
            "PYTHONPATH=control/tools/game-exp python -",
            text,
        )


if __name__ == "__main__":
    unittest.main()
