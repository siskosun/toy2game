from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from protocol_core import ProtocolError, digest_object

EXP_RE = re.compile(r"^EXP-([1-9][0-9]*)$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SAFE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class InitError(RuntimeError):
    def __init__(self, message: str, *, code: str = "INITIALIZATION_CONFLICT"):
        super().__init__(message)
        self.code = code


def run(args: list[str], *, cwd: Path | None = None, env=None, check: bool = True):
    proc = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and proc.returncode != 0:
        raise InitError(
            f"command failed ({proc.returncode}): {' '.join(args)}\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return proc


def run_bytes(args: list[str], *, cwd: Path | None = None, env=None, check: bool = True):
    proc = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and proc.returncode != 0:
        raise InitError(
            f"command failed ({proc.returncode}): {' '.join(args)}\n"
            f"stderr:\n{proc.stderr.decode('utf-8', errors='replace')}"
        )
    return proc


def emit(status: str, **fields):
    value = {"status": status, **fields}
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return value


def _github_raw(repo: str, path: str, ref: str) -> bytes:
    token = os.environ.get("GAME_EXP_GITHUB_TOKEN")
    if not token:
        raise InitError("GAME_EXP_GITHUB_TOKEN is required", code="INITIALIZATION_INVALID")
    encoded_path = "/".join(urllib.parse.quote(part, safe="") for part in path.split("/"))
    query = urllib.parse.urlencode({"ref": ref})
    url = f"https://api.github.com/repos/{repo}/contents/{encoded_path}?{query}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github.raw+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "game-exp-source-initializer",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[-1000:]
        raise InitError(
            f"failed reading authoritative Ledger file {path}: HTTP {exc.code} {body}",
            code="INITIALIZATION_INVALID",
        ) from exc
    except Exception as exc:
        raise InitError(
            f"failed reading authoritative Ledger file {path}: {exc}",
            code="INITIALIZATION_INVALID",
        ) from exc


def _ledger_json(repo: str, path: str) -> dict[str, Any]:
    raw = _github_raw(repo, path, "game-exp/ledger")
    try:
        value = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise InitError(f"invalid JSON in Ledger file {path}: {exc}", code="INITIALIZATION_INVALID") from exc
    if not isinstance(value, dict):
        raise InitError(f"Ledger file {path} must be a JSON object", code="INITIALIZATION_INVALID")
    return value


def _expected_domain_paths(experiment_id: str) -> list[str]:
    root = f"experiments/{experiment_id}"
    return sorted([f"{root}/binding.json", f"{root}/manifest.json", f"{root}/state.json"])


def load_authoritative_binding(repo: str, experiment_id: str):
    match = EXP_RE.fullmatch(experiment_id)
    if not match:
        raise InitError("experiment_id must be EXP-<positive integer>", code="INITIALIZATION_INVALID")
    issue_number = match.group(1)
    root = f"experiments/{experiment_id}"
    binding = _ledger_json(repo, f"{root}/binding.json")
    manifest = _ledger_json(repo, f"{root}/manifest.json")
    state = _ledger_json(repo, f"{root}/state.json")

    if binding.get("kind") != "experiment_identity" or binding.get("experiment_id") != experiment_id:
        raise InitError("binding identity mismatch", code="INITIALIZATION_INVALID")
    if state.get("kind") != "experiment_state" or state.get("experiment_id") != experiment_id:
        raise InitError("state identity mismatch", code="INITIALIZATION_INVALID")

    request_id = binding.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        raise InitError("binding request_id missing", code="INITIALIZATION_INVALID")
    operation = _ledger_json(repo, f"operations/{request_id}.json")

    payload = operation.get("payload")
    stored_digest = operation.get("payload_digest")
    if not isinstance(payload, dict) or not isinstance(stored_digest, str):
        raise InitError("binding operation record is incomplete", code="INITIALIZATION_INVALID")
    actual_digest = digest_object(payload)
    if actual_digest != stored_digest:
        raise InitError(
            f"binding operation payload digest mismatch: stored={stored_digest} actual={actual_digest}",
            code="INITIALIZATION_INVALID",
        )
    if binding.get("inputs_digest") != stored_digest:
        raise InitError("binding inputs_digest does not match operation payload_digest", code="INITIALIZATION_INVALID")
    if operation.get("domain_status") != "APPLIED" or operation.get("domain_experiment_id") != experiment_id:
        raise InitError("binding operation was not authoritatively applied", code="INITIALIZATION_INVALID")
    if sorted(operation.get("domain_paths") or []) != _expected_domain_paths(experiment_id):
        raise InitError("binding operation domain_paths mismatch", code="INITIALIZATION_INVALID")

    if payload.get("kind") != "operation_request" or payload.get("operation") != "experiment.bind":
        raise InitError("binding operation payload type mismatch", code="INITIALIZATION_INVALID")
    input_value = payload.get("input")
    if not isinstance(input_value, dict) or input_value.get("manifest") != manifest:
        raise InitError("manifest snapshot differs from the bound operation payload", code="INITIALIZATION_INVALID")

    init = binding.get("initialization")
    if not isinstance(init, dict):
        raise InitError("binding initialization plan missing", code="INITIALIZATION_INVALID")
    plan_digest = init.get("initialization_plan_digest")
    plan_value = dict(init)
    plan_value.pop("initialization_plan_digest", None)
    if plan_digest != digest_object(plan_value):
        raise InitError("initialization plan digest mismatch", code="INITIALIZATION_INVALID")
    if init.get("manifest_digest") != digest_object(manifest):
        raise InitError("manifest digest mismatch", code="INITIALIZATION_INVALID")

    canonical = binding.get("canonical")
    if not isinstance(canonical, dict) or canonical.get("issue_number") != issue_number:
        raise InitError("binding canonical issue number mismatch", code="INITIALIZATION_INVALID")

    expected = {
        "branch_ref": f"refs/heads/exp/{issue_number}",
        "base_tag_ref": f"refs/tags/exp-base/{issue_number}",
        "final_tag_ref": f"refs/tags/exp-final/{issue_number}",
        "manifest_path": f"experiments/{experiment_id}/manifest.yaml",
        "manifest_snapshot_path": f"experiments/{experiment_id}/manifest.json",
    }
    for key, value in expected.items():
        if init.get(key) != value:
            raise InitError(f"binding initialization {key} is not canonical", code="INITIALIZATION_INVALID")

    parent_sha = binding.get("parent_sha")
    if not isinstance(parent_sha, str) or not SHA_RE.fullmatch(parent_sha):
        raise InitError("binding parent_sha invalid", code="INITIALIZATION_INVALID")
    if manifest.get("parent", {}).get("commit") != parent_sha:
        raise InitError("manifest parent differs from binding parent_sha", code="INITIALIZATION_INVALID")

    return binding, manifest, state, operation


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    raise InitError(f"unsupported manifest scalar {type(value).__name__}", code="INITIALIZATION_INVALID")


def _yaml_lines(value: Any, indent: int = 0) -> list[str]:
    prefix = " " * indent
    if isinstance(value, dict):
        lines: list[str] = []
        for key in sorted(value):
            if not isinstance(key, str) or not SAFE_KEY_RE.fullmatch(key):
                raise InitError(f"unsupported manifest key {key!r}", code="INITIALIZATION_INVALID")
            item = value[key]
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}{key}:")
                lines.extend(_yaml_lines(item, indent + 2))
            else:
                lines.append(f"{prefix}{key}: {_yaml_scalar(item)}")
        return lines
    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}-")
                lines.extend(_yaml_lines(item, indent + 2))
            else:
                lines.append(f"{prefix}- {_yaml_scalar(item)}")
        if not lines:
            lines.append(f"{prefix}[]")
        return lines
    return [f"{prefix}{_yaml_scalar(value)}"]


def manifest_yaml_bytes(manifest: dict[str, Any]) -> bytes:
    return ("\n".join(_yaml_lines(manifest)) + "\n").encode("utf-8")


def _git_env(ssh_key: str | None):
    env = os.environ.copy()
    if ssh_key:
        env["GIT_SSH_COMMAND"] = (
            f'ssh -i "{ssh_key}" -o IdentitiesOnly=yes '
            "-o StrictHostKeyChecking=accept-new"
        )
    return env


def _remote_ref(remote_url: str, ref: str, *, env) -> str | None:
    proc = run(["git", "ls-remote", remote_url, ref], env=env, check=False)
    if proc.returncode != 0:
        raise InitError(f"git ls-remote failed for {ref}: {proc.stderr}")
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    if not lines:
        return None
    if len(lines) != 1:
        raise InitError(f"unexpected multiple remote matches for {ref}")
    sha, name = lines[0].split("\t", 1)
    if name != ref or not SHA_RE.fullmatch(sha):
        raise InitError(f"invalid remote ref response for {ref}: {lines[0]}")
    return sha


def _tag_metadata(repo_dir: Path, tag_ref: str):
    if run(["git", "cat-file", "-t", tag_ref], cwd=repo_dir).stdout.strip() != "tag":
        raise InitError("base tag must be an annotated tag")
    text = run(["git", "cat-file", "-p", tag_ref], cwd=repo_dir).stdout
    header, _, message = text.partition("\n\n")
    fields = {}
    for line in header.splitlines():
        if " " in line:
            key, value = line.split(" ", 1)
            fields[key] = value
    init_match = re.search(r"^game-exp-initialization-commit: ([0-9a-f]{40})$", message, re.MULTILINE)
    plan_match = re.search(r"^game-exp-initialization-plan: (sha256:[0-9a-f]{64})$", message, re.MULTILINE)
    if fields.get("type") != "commit" or not SHA_RE.fullmatch(fields.get("object", "")):
        raise InitError("base tag does not point to a commit")
    if not init_match or not plan_match:
        raise InitError("base tag is missing initialization metadata")
    return fields["object"], init_match.group(1), plan_match.group(1)


def _verify_initialization(
    repo_dir: Path,
    *,
    branch_ref: str,
    tag_ref: str,
    parent_sha: str,
    manifest_path: str,
    manifest_bytes: bytes,
    plan_digest: str,
):
    branch_sha = run(["git", "rev-parse", branch_ref], cwd=repo_dir).stdout.strip()
    tag_parent, init_sha, tag_plan = _tag_metadata(repo_dir, tag_ref)
    if tag_parent != parent_sha:
        raise InitError(f"base tag target mismatch: expected {parent_sha}, got {tag_parent}")
    if tag_plan != plan_digest:
        raise InitError("base tag initialization plan digest mismatch")

    parents = run(["git", "rev-list", "--parents", "-n", "1", init_sha], cwd=repo_dir).stdout.strip().split()
    if len(parents) != 2 or parents[0] != init_sha or parents[1] != parent_sha:
        raise InitError("initialization commit does not have the frozen parent")

    names = [
        line.strip()
        for line in run(
            ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", init_sha],
            cwd=repo_dir,
        ).stdout.splitlines()
        if line.strip()
    ]
    if names != [manifest_path]:
        raise InitError(f"initialization commit changed unexpected paths: {names}")

    actual_manifest = run_bytes(["git", "show", f"{init_sha}:{manifest_path}"], cwd=repo_dir).stdout
    if actual_manifest != manifest_bytes:
        raise InitError("initialization manifest bytes differ from the authoritative snapshot")

    ancestor = run(["git", "merge-base", "--is-ancestor", init_sha, branch_sha], cwd=repo_dir, check=False)
    if ancestor.returncode != 0:
        raise InitError("experiment branch is not descended from the initialization commit")

    return branch_sha, init_sha


def initialize_source(
    *,
    remote_url: str,
    experiment_id: str,
    binding: dict[str, Any],
    manifest: dict[str, Any],
    state: dict[str, Any],
    ssh_key: str | None = None,
):
    init = binding["initialization"]
    branch_ref = init["branch_ref"]
    tag_ref = init["base_tag_ref"]
    parent_sha = binding["parent_sha"]
    manifest_path = init["manifest_path"]
    plan_digest = init["initialization_plan_digest"]
    manifest_bytes = manifest_yaml_bytes(manifest)

    if state.get("lifecycle") != "ACTIVE":
        raise InitError(
            f"source initialization requires ACTIVE lifecycle, got {state.get('lifecycle')}",
            code="INITIALIZATION_INVALID",
        )
    if state.get("archive_lock") is not None:
        raise InitError("source initialization blocked by archive lock", code="INITIALIZATION_INVALID")

    env = _git_env(ssh_key)
    branch_remote = _remote_ref(remote_url, branch_ref, env=env)
    tag_remote = _remote_ref(remote_url, tag_ref, env=env)
    if (branch_remote is None) != (tag_remote is None):
        raise InitError("partial source initialization: branch/tag presence differs")

    with tempfile.TemporaryDirectory(prefix="game-exp-source-init-") as td:
        repo_dir = Path(td) / "repo"
        run(["git", "init", str(repo_dir)])
        run(["git", "remote", "add", "origin", remote_url], cwd=repo_dir)
        run(["git", "config", "user.name", "game-exp-source-initializer"], cwd=repo_dir)
        run(
            ["git", "config", "user.email", "game-exp-source-initializer@users.noreply.github.com"],
            cwd=repo_dir,
        )

        if branch_remote is not None:
            run(
                [
                    "git",
                    "fetch",
                    "--no-tags",
                    "origin",
                    f"{branch_ref}:refs/remotes/game-exp/branch",
                    f"{tag_ref}:{tag_ref}",
                ],
                cwd=repo_dir,
                env=env,
            )
            branch_sha, init_sha = _verify_initialization(
                repo_dir,
                branch_ref="refs/remotes/game-exp/branch",
                tag_ref=tag_ref,
                parent_sha=parent_sha,
                manifest_path=manifest_path,
                manifest_bytes=manifest_bytes,
                plan_digest=plan_digest,
            )
            return {
                "status": "INITIALIZED",
                "experiment_id": experiment_id,
                "branch_ref": branch_ref,
                "branch_head": branch_sha,
                "base_tag_ref": tag_ref,
                "parent_sha": parent_sha,
                "initialization_commit": init_sha,
                "replayed": True,
            }

        fetch_parent = run(["git", "fetch", "--no-tags", "origin", parent_sha], cwd=repo_dir, env=env, check=False)
        if fetch_parent.returncode != 0:
            raise InitError(
                f"cannot fetch frozen parent {parent_sha}: {fetch_parent.stderr}",
                code="INITIALIZATION_INVALID",
            )
        run(["git", "checkout", "--detach", parent_sha], cwd=repo_dir)

        target = repo_dir / manifest_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(manifest_bytes)
        run(["git", "add", manifest_path], cwd=repo_dir)
        changed = [
            line.strip()
            for line in run(["git", "diff", "--cached", "--name-only"], cwd=repo_dir).stdout.splitlines()
            if line.strip()
        ]
        if changed != [manifest_path]:
            raise InitError(f"initialization staging changed unexpected paths: {changed}")

        run(["git", "commit", "--no-gpg-sign", "-m", f"game-exp initialize {experiment_id}"], cwd=repo_dir)
        init_sha = run(["git", "rev-parse", "HEAD"], cwd=repo_dir).stdout.strip()

        tag_name = tag_ref.removeprefix("refs/tags/")
        message = (
            f"game-exp base {experiment_id}\n\n"
            f"game-exp-initialization-commit: {init_sha}\n"
            f"game-exp-initialization-plan: {plan_digest}\n"
        )
        run(["git", "tag", "-a", tag_name, parent_sha, "-m", message], cwd=repo_dir)

        push = run(
            [
                "git",
                "push",
                "--atomic",
                "origin",
                f"{init_sha}:{branch_ref}",
                f"{tag_ref}:{tag_ref}",
            ],
            cwd=repo_dir,
            env=env,
            check=False,
        )
        if push.returncode != 0:
            branch_after = _remote_ref(remote_url, branch_ref, env=env)
            tag_after = _remote_ref(remote_url, tag_ref, env=env)
            if branch_after is None or tag_after is None:
                raise InitError(
                    f"atomic source initialization push failed: {push.stderr}",
                    code="INITIALIZATION_CONFLICT",
                )
            run(
                [
                    "git",
                    "fetch",
                    "--no-tags",
                    "origin",
                    f"{branch_ref}:refs/remotes/game-exp/branch",
                    f"{tag_ref}:{tag_ref}",
                ],
                cwd=repo_dir,
                env=env,
            )
            branch_sha, recovered_init = _verify_initialization(
                repo_dir,
                branch_ref="refs/remotes/game-exp/branch",
                tag_ref=tag_ref,
                parent_sha=parent_sha,
                manifest_path=manifest_path,
                manifest_bytes=manifest_bytes,
                plan_digest=plan_digest,
            )
            return {
                "status": "INITIALIZED",
                "experiment_id": experiment_id,
                "branch_ref": branch_ref,
                "branch_head": branch_sha,
                "base_tag_ref": tag_ref,
                "parent_sha": parent_sha,
                "initialization_commit": recovered_init,
                "replayed": True,
                "recovered_after_push_uncertainty": True,
            }

        return {
            "status": "INITIALIZED",
            "experiment_id": experiment_id,
            "branch_ref": branch_ref,
            "branch_head": init_sha,
            "base_tag_ref": tag_ref,
            "parent_sha": parent_sha,
            "initialization_commit": init_sha,
            "replayed": False,
        }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--experiment-id", required=True)
    ap.add_argument("--ssh-key", required=True)
    args = ap.parse_args()

    binding, manifest, state, _operation = load_authoritative_binding(args.repo, args.experiment_id)
    result = initialize_source(
        remote_url=f"git@github.com:{args.repo}.git",
        experiment_id=args.experiment_id,
        binding=binding,
        manifest=manifest,
        state=state,
        ssh_key=args.ssh_key,
    )
    emit(**result)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ProtocolError, InitError) as exc:
        code = exc.code if isinstance(exc, InitError) else "INITIALIZATION_INVALID"
        emit(code, error=str(exc))
        raise SystemExit(46)
