from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import pathlib
import subprocess
import tarfile
from typing import Any

from protocol_core import digest_object

POLICY_PATH = ".game-exp/project-policy.json"
_ALLOWED_TOP = {"schema_version", "adapter", "install", "test", "build", "candidate"}
_ALLOWED_STEP = {"argv"}
_ALLOWED_CANDIDATE = {"include", "required_paths"}


class ProjectPolicyError(ValueError):
    pass


def _strict_keys(value: dict[str, Any], allowed: set[str], where: str) -> None:
    extra = set(value) - allowed
    missing = allowed - set(value)
    if extra or missing:
        raise ProjectPolicyError(
            f"{where}: keys mismatch; missing={sorted(missing)} extra={sorted(extra)}"
        )
def _safe_rel_path(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ProjectPolicyError(f"{where}: expected non-empty normalized relative path")
    if "\\" in value or value.startswith("/") or value.startswith("~"):
        raise ProjectPolicyError(f"{where}: unsafe path")
    parts = pathlib.PurePosixPath(value).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise ProjectPolicyError(f"{where}: unsafe path")
    return value.rstrip("/")


def _argv(value: Any, where: str) -> list[str]:
    if not isinstance(value, dict):
        raise ProjectPolicyError(f"{where}: expected object")
    _strict_keys(value, _ALLOWED_STEP, where)
    argv = value["argv"]
    if not isinstance(argv, list) or not argv:
        raise ProjectPolicyError(f"{where}.argv: expected non-empty array")
    if any(not isinstance(item, str) or not item for item in argv):
        raise ProjectPolicyError(f"{where}.argv: expected non-empty strings")
    return list(argv)


def validate_policy(policy: Any) -> dict[str, Any]:
    if not isinstance(policy, dict):
        raise ProjectPolicyError("policy: expected object")
    _strict_keys(policy, _ALLOWED_TOP, "policy")
    if policy["schema_version"] != 1:
        raise ProjectPolicyError("policy.schema_version must equal 1")
    if policy["adapter"] != "node-npm":
        raise ProjectPolicyError("policy.adapter must equal 'node-npm' in schema v1")

    for name in ("install", "test", "build"):
        _argv(policy[name], f"policy.{name}")

    candidate = policy["candidate"]
    if not isinstance(candidate, dict):
        raise ProjectPolicyError("policy.candidate: expected object")
    _strict_keys(candidate, _ALLOWED_CANDIDATE, "policy.candidate")
    for field in ("include", "required_paths"):
        values = candidate[field]
        if not isinstance(values, list) or not values:
            raise ProjectPolicyError(f"policy.candidate.{field}: expected non-empty array")
        candidate[field] = [
            _safe_rel_path(item, f"policy.candidate.{field}") for item in values
        ]
    return policy


def load_policy(path: str | os.PathLike[str] = POLICY_PATH) -> dict[str, Any]:
    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    return validate_policy(data)
def policy_digest(policy: dict[str, Any]) -> str:
    return digest_object(validate_policy(json.loads(json.dumps(policy))))


def run_phase(policy: dict[str, Any], phase: str, cwd: str | os.PathLike[str]) -> None:
    if phase not in {"install", "test", "build"}:
        raise ProjectPolicyError(f"unknown phase: {phase}")
    argv = _argv(policy[phase], f"policy.{phase}")
    subprocess.run(argv, cwd=cwd, check=True, shell=False)


def _iter_candidate_files(root: pathlib.Path, includes: list[str]) -> list[tuple[pathlib.Path, str]]:
    items: list[tuple[pathlib.Path, str]] = []
    seen: set[str] = set()
    root_resolved = root.resolve()
    for rel in includes:
        declared = root / rel
        if declared.is_symlink():
            raise ProjectPolicyError(f"candidate include symlink is forbidden: {rel}")
        source = declared.resolve()
        if source != root_resolved and root_resolved not in source.parents:
            raise ProjectPolicyError(f"candidate include escapes repository: {rel}")
        if not source.exists():
            raise ProjectPolicyError(f"candidate include missing: {rel}")
        paths = [source] if source.is_file() else sorted(p for p in source.rglob("*") if p.is_file())
        for path in paths:
            if path.is_symlink():
                raise ProjectPolicyError(f"candidate file symlink is forbidden: {path}")
            resolved = path.resolve()
            if root_resolved not in resolved.parents:
                raise ProjectPolicyError(f"candidate file escapes repository: {path}")
            arcname = path.relative_to(root).as_posix()
            if arcname in seen:
                raise ProjectPolicyError(f"candidate path included more than once: {arcname}")
            seen.add(arcname)
            items.append((path, arcname))
    return sorted(items, key=lambda item: item[1])
def package_candidate(
    policy: dict[str, Any],
    repo_root: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
) -> None:
    policy = validate_policy(json.loads(json.dumps(policy)))
    root = pathlib.Path(repo_root).resolve()
    files = _iter_candidate_files(root, policy["candidate"]["include"])
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as tf:
        for path, arcname in files:
            info = tf.gettarinfo(str(path), arcname=arcname)
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = 0
            with path.open("rb") as fh:
                tf.addfile(info, fh)
    with pathlib.Path(output_path).open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as gz:
            gz.write(buffer.getvalue())


def verify_candidate_archive(
    policy: dict[str, Any], archive_path: str | os.PathLike[str]
) -> list[str]:
    policy = validate_policy(json.loads(json.dumps(policy)))
    with tarfile.open(archive_path, mode="r:gz") as tf:
        names = []
        seen: set[str] = set()
        for member in tf.getmembers():
            name = _safe_rel_path(member.name.rstrip("/"), "candidate archive member")
            if not member.isfile():
                raise ProjectPolicyError(f"candidate archive contains non-file entry: {name}")
            if name in seen:
                raise ProjectPolicyError(f"candidate archive contains duplicate path: {name}")
            seen.add(name)
            names.append(name)
    name_set = set(names)
    for required in policy["candidate"]["required_paths"]:
        if required in name_set:
            continue
        prefix = required.rstrip("/") + "/"
        if any(name.startswith(prefix) for name in names):
            continue
        raise ProjectPolicyError(f"candidate archive missing required path: {required}")
    return sorted(name_set)


def _main() -> int:
    parser = argparse.ArgumentParser(description="Run trusted game-exp project policy")
    parser.add_argument("--policy", default=POLICY_PATH)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("digest")
    run = sub.add_parser("run")
    run.add_argument("phase", choices=("install", "test", "build"))
    run.add_argument("--cwd", default=".")

    package = sub.add_parser("package")
    package.add_argument("--repo-root", default=".")
    package.add_argument("--output", required=True)

    verify = sub.add_parser("verify")
    verify.add_argument("--archive", required=True)

    args = parser.parse_args()
    policy = load_policy(args.policy)
    if args.command == "digest":
        print(policy_digest(policy))
    elif args.command == "run":
        run_phase(policy, args.phase, args.cwd)
    elif args.command == "package":
        package_candidate(policy, args.repo_root, args.output)
    elif args.command == "verify":
        for name in verify_candidate_archive(policy, args.archive):
            print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())