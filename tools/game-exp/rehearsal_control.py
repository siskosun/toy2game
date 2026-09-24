from __future__ import annotations

import argparse
import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

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


def rehearsal_policy_digest() -> str:
    return digest_object(
        {
            "schema_version": 1,
            "merge_mode": "two-parent-no-ff",
            "base": "current-main-at-prepare",
            "candidate": "current-candidate-source",
            "required_checks": [
                "merge",
                "project_tests",
                "build",
                "trusted_tree_recompute",
                "main_freshness",
            ],
        }
    )


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
    if state.get("lifecycle") != "PROMISING":
        raise RehearsalControlError(
            f"Rehearsal requires PROMISING lifecycle, got {state.get('lifecycle')!r}"
        )
    candidate_id = state.get("current_candidate_id")
    if not isinstance(candidate_id, str) or not candidate_id:
        raise RehearsalControlError("PROMISING experiment has no current Candidate")
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
        "main_sha": main_sha,
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
    )
    values = {name: str(getattr(args, name)) for name in names}
    context = {
        **values,
        "checks": [
            {"name": "merge", "status": "PASS", "source": "TRUSTED_OBSERVED"},
            {"name": "project_tests", "status": "PASS", "source": "TRUSTED_OBSERVED"},
            {"name": "build", "status": "PASS", "source": "TRUSTED_OBSERVED"},
            {
                "name": "trusted_tree_recompute",
                "status": "PASS",
                "source": "TRUSTED_OBSERVED",
            },
            {
                "name": "main_freshness",
                "status": "PASS",
                "source": "TRUSTED_OBSERVED",
            },
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
    ):
        q.add_argument("--" + name, required=True)
    q.add_argument("--context-output", required=True)
    return ap


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "prepare":
        result = prepare(args)
    else:
        result = payload(args)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RehearsalControlError as exc:
        print(json.dumps({"status": "REJECTED", "error": str(exc)}, sort_keys=True))
        raise SystemExit(2)
