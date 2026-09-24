from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from domain_core import DomainError, REHEARSAL_REQUIRED_CHECKS, rehearsal_policy_digest, validate_rehearsal_scope_paths
from protocol_core import build_operation_payload, canonical_json_bytes, digest_object

EXP_RE = re.compile(r"^EXP-([1-9][0-9]*)$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class RehearsalControlError(RuntimeError):
    pass


def github_json(repo: str, suffix: str) -> dict[str, Any]:
    token = os.environ.get("GAME_EXP_GITHUB_TOKEN")
    if not token:
        raise RehearsalControlError("GAME_EXP_GITHUB_TOKEN is required")
    req = urllib.request.Request(
        "https://api.github.com/repos/" + repo + suffix,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "game-exp-rehearsal-control",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            value = json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[-1000:]
        raise RehearsalControlError(
            f"GitHub API failed ({exc.code}) for {suffix}: {body}"
        ) from exc
    except Exception as exc:
        raise RehearsalControlError(f"GitHub API failed for {suffix}: {exc}") from exc
    if not isinstance(value, dict):
        raise RehearsalControlError(f"GitHub API returned non-object for {suffix}")
    return value


def github_content_json(repo: str, path: str, ref: str) -> dict[str, Any]:
    suffix = (
        "/contents/"
        + "/".join(urllib.parse.quote(part, safe="") for part in path.split("/"))
        + "?ref="
        + urllib.parse.quote(ref, safe="")
    )
    value = github_json(repo, suffix)
    if value.get("encoding") != "base64" or not isinstance(value.get("content"), str):
        raise RehearsalControlError(f"GitHub content is not base64 JSON: {path}@{ref}")
    try:
        raw = base64.b64decode(value["content"], validate=False)
        parsed = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise RehearsalControlError(f"invalid JSON at {path}@{ref}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RehearsalControlError(f"expected JSON object at {path}@{ref}")
    return parsed


def _glob_regex(pattern: str) -> re.Pattern[str]:
    if not isinstance(pattern, str) or not pattern:
        raise RehearsalControlError("scope pattern must be a non-empty string")
    if "\\" in pattern or pattern.startswith("/"):
        raise RehearsalControlError(f"scope pattern is not portable: {pattern!r}")
    out = ["^"]
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*":
            if i + 1 < len(pattern) and pattern[i + 1] == "*":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(ch))
        i += 1
    out.append("$")
    return re.compile("".join(out))


def _matches_any(path: str, patterns: list[str]) -> bool:
    return any(_glob_regex(pattern).fullmatch(path) is not None for pattern in patterns)


def _decode_scope(scope_b64: str) -> dict[str, Any]:
    try:
        value = json.loads(base64.b64decode(scope_b64).decode("utf-8"))
    except Exception as exc:
        raise RehearsalControlError(f"invalid scope payload: {exc}") from exc
    if not isinstance(value, dict) or set(value) != {"allowed", "avoid", "metadata_paths"}:
        raise RehearsalControlError("scope payload keys are invalid")
    allowed = value["allowed"]
    avoid = value["avoid"]
    metadata_paths = value["metadata_paths"]
    if (
        not isinstance(allowed, list)
        or not allowed
        or not all(isinstance(x, str) and x for x in allowed)
        or not isinstance(avoid, list)
        or not all(isinstance(x, str) and x for x in avoid)
        or not isinstance(metadata_paths, list)
        or not all(isinstance(x, str) and x for x in metadata_paths)
    ):
        raise RehearsalControlError("scope payload values are invalid")
    return {
        "allowed": allowed,
        "avoid": avoid,
        "metadata_paths": metadata_paths,
    }


def _git(args: list[str], *, cwd: Path, input_bytes: bytes | None = None) -> bytes:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace")[-2000:]
        raise RehearsalControlError(f"git {' '.join(args)} failed: {err}")
    return proc.stdout


def integration_tree(args: argparse.Namespace) -> dict[str, Any]:
    repo_dir = Path(args.repo_dir).resolve()
    for name, value in (
        ("base_sha", args.base_sha),
        ("source_sha", args.source_sha),
        ("main_sha", args.main_sha),
    ):
        if not SHA_RE.fullmatch(value):
            raise RehearsalControlError(f"{name} must be a 40-character SHA")
    scope = _decode_scope(args.scope_b64)

    head = _git(["rev-parse", "HEAD"], cwd=repo_dir).decode("ascii").strip()
    if head != args.main_sha:
        raise RehearsalControlError(
            f"working tree HEAD differs from expected main: {head} != {args.main_sha}"
        )

    ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", args.base_sha, args.source_sha],
        cwd=repo_dir,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=False,
    )
    if ancestry.returncode != 0:
        raise RehearsalControlError(
            "Candidate source is not a descendant of the authoritative binding parent"
        )

    raw = _git(
        [
            "diff",
            "--name-only",
            "--no-renames",
            "-z",
            args.base_sha,
            args.source_sha,
        ],
        cwd=repo_dir,
    )
    try:
        changed = [item.decode("utf-8") for item in raw.split(b"\0") if item]
    except UnicodeDecodeError as exc:
        raise RehearsalControlError("candidate changed a non-UTF-8 path") from exc

    if len(scope["metadata_paths"]) != 1:
        raise RehearsalControlError("scope must contain exactly one canonical manifest path")
    manifest_path = scope["metadata_paths"][0]
    try:
        checked = validate_rehearsal_scope_paths(
            changed,
            allowed=scope["allowed"],
            avoid=scope["avoid"],
            manifest_path=manifest_path,
        )
    except DomainError as exc:
        raise RehearsalControlError(str(exc)) from exc
    ignored_metadata = [path for path in checked if path == manifest_path]
    included = [path for path in checked if path != manifest_path]

    if not included:
        raise RehearsalControlError(
            "candidate has no integratable changes inside manifest scope"
        )

    _git(["reset", "--hard", args.main_sha], cwd=repo_dir)
    patch = _git(
        [
            "diff",
            "--binary",
            "--full-index",
            args.base_sha,
            args.source_sha,
            "--",
            *included,
        ],
        cwd=repo_dir,
    )
    if not patch:
        raise RehearsalControlError("scope-filtered candidate patch is empty")
    _git(["apply", "--index", "--3way", "-"], cwd=repo_dir, input_bytes=patch)
    tree = _git(["write-tree"], cwd=repo_dir).decode("ascii").strip()
    if not SHA_RE.fullmatch(tree):
        raise RehearsalControlError("integration tree is not a commit-tree SHA")

    return {
        "integration_tree_sha": tree,
        "included_paths": sorted(included),
        "ignored_metadata_paths": sorted(ignored_metadata),
        "scope_digest": digest_object(scope),
    }


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    match = EXP_RE.fullmatch(args.experiment_id)
    if not match:
        raise RehearsalControlError("experiment_id must be EXP-<positive integer>")
    issue_number = match.group(1)
    state = github_content_json(
        args.repo,
        f"experiments/{args.experiment_id}/state.json",
        "game-exp/ledger",
    )
    if state.get("lifecycle") not in {"PROMISING", "SELECTED"}:
        raise RehearsalControlError(
            f"Rehearsal requires PROMISING or SELECTED lifecycle, got {state.get('lifecycle')!r}"
        )
    candidate_id = state.get("current_candidate_id")
    if not isinstance(candidate_id, str) or not candidate_id:
        raise RehearsalControlError("experiment has no current Candidate")
    candidate = github_content_json(
        args.repo,
        f"experiments/{args.experiment_id}/candidates/{candidate_id}.json",
        "game-exp/ledger",
    )
    if (
        candidate.get("kind") != "candidate"
        or candidate.get("experiment_id") != args.experiment_id
        or candidate.get("candidate_id") != candidate_id
    ):
        raise RehearsalControlError("current Candidate identity is invalid")
    source_sha = candidate.get("source_sha")
    if not isinstance(source_sha, str) or not SHA_RE.fullmatch(source_sha):
        raise RehearsalControlError("current Candidate source SHA is invalid")

    binding = github_content_json(
        args.repo,
        f"experiments/{args.experiment_id}/binding.json",
        "game-exp/ledger",
    )
    manifest = github_content_json(
        args.repo,
        f"experiments/{args.experiment_id}/manifest.json",
        "game-exp/ledger",
    )
    if binding.get("experiment_id") != args.experiment_id:
        raise RehearsalControlError("binding experiment identity mismatch")
    initialization = binding.get("initialization")
    if not isinstance(initialization, dict):
        raise RehearsalControlError("binding initialization is missing")
    manifest_digest = digest_object(manifest)
    if (
        initialization.get("manifest_digest") != manifest_digest
        or candidate.get("manifest_digest") != manifest_digest
    ):
        raise RehearsalControlError("Candidate/Binding manifest digest mismatch")
    base_sha = binding.get("parent_sha")
    if not isinstance(base_sha, str) or not SHA_RE.fullmatch(base_sha):
        raise RehearsalControlError("binding parent SHA is invalid")
    manifest_path = initialization.get("manifest_path")
    if not isinstance(manifest_path, str) or not manifest_path:
        raise RehearsalControlError("binding manifest path is invalid")
    scope = manifest.get("scope")
    if not isinstance(scope, dict) or set(scope) != {"allowed", "avoid"}:
        raise RehearsalControlError("manifest scope is invalid")
    allowed = scope.get("allowed")
    avoid = scope.get("avoid")
    if (
        not isinstance(allowed, list)
        or not allowed
        or not all(isinstance(x, str) and x for x in allowed)
        or not isinstance(avoid, list)
        or not all(isinstance(x, str) and x for x in avoid)
    ):
        raise RehearsalControlError("manifest scope values are invalid")
    scope_value = {
        "allowed": list(allowed),
        "avoid": list(avoid),
        "metadata_paths": [manifest_path],
    }
    scope_b64 = base64.b64encode(canonical_json_bytes(scope_value)).decode("ascii")

    main_ref = github_json(args.repo, "/git/ref/heads/main")
    main_sha = (main_ref.get("object") or {}).get("sha")
    if not isinstance(main_sha, str) or not SHA_RE.fullmatch(main_sha):
        raise RehearsalControlError("main ref did not resolve to a commit SHA")

    if not re.fullmatch(r"[0-9]+", args.run_id):
        raise RehearsalControlError("run_id must be decimal")
    if not re.fullmatch(r"[1-9][0-9]*", args.run_attempt):
        raise RehearsalControlError("run_attempt must be positive decimal")
    if not SHA_RE.fullmatch(args.workflow_source_sha):
        raise RehearsalControlError("workflow_source_sha must be a 40-character SHA")

    rehearsal_id = f"R-{issue_number}-{args.run_id}-{args.run_attempt}"
    rehearsal_ref = f"refs/tags/exp-rehearsal/{issue_number}/{rehearsal_id}"
    return {
        "experiment_id": args.experiment_id,
        "issue_number": issue_number,
        "candidate_id": candidate_id,
        "source_sha": source_sha,
        "base_sha": base_sha,
        "main_sha": main_sha,
        "scope_b64": scope_b64,
        "rehearsal_id": rehearsal_id,
        "rehearsal_ref": rehearsal_ref,
        "policy_digest": rehearsal_policy_digest(),
        "workflow_source_sha": args.workflow_source_sha,
        "run_id": args.run_id,
        "run_attempt": args.run_attempt,
    }


def payload(args: argparse.Namespace) -> dict[str, Any]:
    names = (
        "experiment_id",
        "candidate_id",
        "rehearsal_id",
        "main_sha",
        "source_sha",
        "integration_sha",
        "integration_tree_sha",
        "rehearsal_ref",
        "workflow_source_sha",
        "run_id",
        "run_attempt",
        "policy_digest",
        "scope_digest",
    )
    values = {name: str(getattr(args, name)) for name in names}
    context = {
        **values,
        "checks": [
            {"name": name, "status": "PASS", "source": "TRUSTED_OBSERVED"}
            for name in REHEARSAL_REQUIRED_CHECKS
        ],
    }
    Path(args.context_output).write_bytes(canonical_json_bytes(context) + b"\n")
    request = build_operation_payload(
        "rehearsal.register",
        {"experiment_id": args.experiment_id},
        preconditions={
            "candidate_id": args.candidate_id,
            "main_sha": args.main_sha,
        },
    )
    raw = canonical_json_bytes(request)
    return {
        "request_id": f"req-rehearsal-{args.run_id}-{args.run_attempt}",
        "payload_b64": base64.b64encode(raw).decode("ascii"),
        "payload_digest": digest_object(request),
        "context_path": args.context_output,
    }


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("prepare")
    p.add_argument("--repo", required=True)
    p.add_argument("--experiment-id", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--run-attempt", required=True)
    p.add_argument("--workflow-source-sha", required=True)

    q = sub.add_parser("payload")
    for name in (
        "experiment-id",
        "candidate-id",
        "rehearsal-id",
        "main-sha",
        "source-sha",
        "integration-sha",
        "integration-tree-sha",
        "rehearsal-ref",
        "workflow-source-sha",
        "run-id",
        "run-attempt",
        "policy-digest",
        "scope-digest",
    ):
        q.add_argument("--" + name, required=True)
    q.add_argument("--context-output", required=True)

    r = sub.add_parser("integration-tree")
    r.add_argument("--repo-dir", required=True)
    r.add_argument("--base-sha", required=True)
    r.add_argument("--source-sha", required=True)
    r.add_argument("--main-sha", required=True)
    r.add_argument("--scope-b64", required=True)
    return ap


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "prepare":
        result = prepare(args)
    elif args.command == "payload":
        result = payload(args)
    else:
        result = integration_tree(args)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RehearsalControlError as exc:
        print(json.dumps({"status": "REJECTED", "error": str(exc)}, sort_keys=True))
        raise SystemExit(2)
