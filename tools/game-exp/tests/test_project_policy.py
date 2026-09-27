from __future__ import annotations

import json
import pathlib
import sys
import tarfile
import tempfile
import unittest

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS_DIR))

from project_policy import (
    ProjectPolicyError,
    package_candidate,
    policy_digest,
    validate_policy,
    verify_candidate_archive,
)


def valid_policy():
    return {
        "schema_version": 1,
        "adapter": "node-npm",
        "install": {"argv": ["npm", "ci"]},
        "test": {"argv": ["npm", "test"]},
        "build": {"argv": ["npm", "run", "build"]},
        "candidate": {"include": ["dist"], "required_paths": ["dist/index.html"]},
    }


def valid_policy_v2(adapter="node-npm"):
    return {
        "schema_version": 2,
        "adapter": adapter,
        "toolchain": {"node_version": "22.21.1"} if adapter == "node-npm" else {},
        "install": {"argv": ["npm", "ci"]} if adapter == "node-npm" else {"argv": ["python", "-m", "pip", "install", "-r", "requirements.txt"]},
        "test": {"argv": ["npm", "test"]} if adapter == "node-npm" else {"argv": ["python", "-m", "pytest"]},
        "build": {"argv": ["npm", "run", "build"]} if adapter == "node-npm" else {"argv": ["python", "build.py"]},
        "candidate": {"include": ["dist"], "required_paths": ["dist/index.html"]},
    }


class ProjectPolicyTests(unittest.TestCase):
    def test_valid_policy_has_stable_digest(self):
        first = policy_digest(valid_policy())
        second = policy_digest(json.loads(json.dumps(valid_policy())))
        self.assertEqual(first, second)
        self.assertRegex(first, r"^sha256:[0-9a-f]{64}$")

    def test_schema_v2_node_policy_is_valid(self):
        policy = valid_policy_v2()
        self.assertEqual(validate_policy(policy)["toolchain"]["node_version"], "22.21.1")

    def test_schema_v2_generic_command_adapter_is_valid(self):
        policy = valid_policy_v2("command")
        self.assertEqual(validate_policy(policy)["adapter"], "command")
        self.assertEqual(validate_policy(policy)["toolchain"], {})

    def test_schema_v1_non_node_adapter_remains_rejected(self):
        policy = valid_policy()
        policy["adapter"] = "command"
        with self.assertRaises(ProjectPolicyError):
            validate_policy(policy)

    def test_schema_v2_node_requires_node_version(self):
        policy = valid_policy_v2()
        policy["toolchain"] = {}
        with self.assertRaises(ProjectPolicyError):
            validate_policy(policy)

    def test_schema_v2_node_rejects_unpinned_version_range(self):
        policy = valid_policy_v2()
        policy["toolchain"]["node_version"] = ">=22"
        with self.assertRaises(ProjectPolicyError):
            validate_policy(policy)

    def test_schema_v2_generic_adapter_rejects_unused_toolchain(self):
        policy = valid_policy_v2("command")
        policy["toolchain"] = {"node_version": "22.21.1"}
        with self.assertRaises(ProjectPolicyError):
            validate_policy(policy)

    def test_schema_v2_adapter_must_be_normalized(self):
        policy = valid_policy_v2("command")
        policy["adapter"] = "Command Adapter"
        with self.assertRaises(ProjectPolicyError):
            validate_policy(policy)

    def test_extra_key_is_rejected(self):
        policy = valid_policy()
        policy["shell"] = "bash"
        with self.assertRaises(ProjectPolicyError):
            validate_policy(policy)

    def test_command_must_be_argv_array(self):
        policy = valid_policy()
        policy["test"] = {"argv": "npm test"}
        with self.assertRaises(ProjectPolicyError):
            validate_policy(policy)

    def test_path_traversal_is_rejected(self):
        policy = valid_policy()
        policy["candidate"]["include"] = ["../secret"]
        with self.assertRaises(ProjectPolicyError):
            validate_policy(policy)

    def test_package_is_deterministic_and_verifiable(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "dist" / "games").mkdir(parents=True)
            (root / "dist" / "index.html").write_text("ok", encoding="utf-8")
            (root / "dist" / "games" / "a.js").write_text("x", encoding="utf-8")
            policy = valid_policy()
            policy["candidate"]["required_paths"] = ["dist/index.html", "dist/games"]
            first = root / "first.tgz"
            second = root / "second.tgz"
            package_candidate(policy, root, first)
            package_candidate(policy, root, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            names = verify_candidate_archive(policy, first)
            self.assertIn("dist/index.html", names)
            self.assertIn("dist/games/a.js", names)

    def test_duplicate_candidate_include_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "dist").mkdir()
            (root / "dist" / "index.html").write_text("ok", encoding="utf-8")
            policy = valid_policy()
            policy["candidate"]["include"] = ["dist", "dist/index.html"]
            with self.assertRaises(ProjectPolicyError):
                package_candidate(policy, root, root / "candidate.tgz")

    def test_duplicate_archive_member_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            archive = pathlib.Path(td) / "duplicate.tgz"
            import io
            with tarfile.open(archive, "w:gz") as tf:
                for _ in range(2):
                    info = tarfile.TarInfo("dist/index.html")
                    info.size = 1
                    tf.addfile(info, io.BytesIO(b"x"))
            with self.assertRaises(ProjectPolicyError):
                verify_candidate_archive(valid_policy(), archive)

    def test_missing_required_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "dist").mkdir()
            (root / "dist" / "other.txt").write_text("x", encoding="utf-8")
            archive = root / "candidate.tgz"
            package_candidate(valid_policy(), root, archive)
            with self.assertRaises(ProjectPolicyError):
                verify_candidate_archive(valid_policy(), archive)

    def test_archive_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            archive = pathlib.Path(td) / "bad.tgz"
            with tarfile.open(archive, "w:gz") as tf:
                info = tarfile.TarInfo("../escape.txt")
                info.size = 1
                import io
                tf.addfile(info, io.BytesIO(b"x"))
            with self.assertRaises(ProjectPolicyError):
                verify_candidate_archive(valid_policy(), archive)


if __name__ == "__main__":
    unittest.main()