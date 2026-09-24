from __future__ import annotations

import argparse
import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from protocol_core import build_operation_payload, canonical_json_bytes, encode_payload_b64


class IntegrationControlError(RuntimeError):
    pass


def github_json(repo: str, suffix: str) -> dict[str, Any]:
    token = os.environ.get("GAME_EXP_GITHUB_TOKEN")
    if not token:
        raise IntegrationControlError("GAME_EXP_GITHUB_TOKEN is required")
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}{suffix}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "game-exp-integration",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            value = json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[-1000:]
        raise IntegrationControlError(
            f"GitHub API failed ({exc.code}) for {suffix}: {body}"
        ) from exc
    if not isinstance(value, dict):
        raise IntegrationControlError(f"GitHub API returned non-object for {suffix}")
    return value


def github_content_json(repo: str, path: str, ref: str) -> dict[str, Any]:
    suffix = (
        "/contents/"
        + "/".join(urllib.parse.quote(part, safe="") for part in path.split("/"))
        + "?ref="
        + urllib.parse.quote(ref, safe="")
    )
    value = github_json(repo, suffix)
    encoded = value.get("content")
    if not isinstance(encoded, str):
        raise IntegrationControlError(f"missing content for {path}@{ref}")
    try:
        decoded = base64.b64decode(encoded).decode("utf-8")
        obj = json.loads(decoded)
    except Exception as exc:
        raise IntegrationControlError(f"invalid JSON for {path}@{ref}: {exc}") from exc
    if not isinstance(obj, dict):
        raise IntegrationControlError(f"{path}@{ref} must be an object")
    return obj


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    experiment_id = args.experiment_id
    match = re.fullmatch(r"EXP-([1-9][0-9]*)", experiment_id)
    if not match:
        raise IntegrationControlError("experiment_id must be EXP-<number>")
    issue = match.group(1)

    state = github_content_json(
        args.repo,
        f"experiments/{experiment_id}/state.json",
        "game-exp/ledger",
    )
    if state.get("lifecycle") != "SELECTED":
        raise IntegrationControlError(
            f"Integration requires SELECTED lifecycle, got {state.get('lifecycle')!r}"
        )
    candidate_id = state.get("current_candidate_id")
    rehearsal_id = state.get("current_rehearsal_id")
    if not isinstance(candidate_id, str) or not isinstance(rehearsal_id, str):
        raise IntegrationControlError("SELECTED experiment lacks current Candidate/Rehearsal")

    rehearsal = github_content_json(
        args.repo,
        f"experiments/{experiment_id}/rehearsals/{rehearsal_id}.json",
        "game-exp/ledger",
    )
    if (
        rehearsal.get("kind") != "rehearsal"
        or rehearsal.get("candidate_id") != candidate_id
        or rehearsal.get("rehearsal_id") != rehearsal_id
    ):
        raise IntegrationControlError("current Rehearsal record mismatch")

    main_sha = rehearsal.get("main_sha")
    integration_tree_sha = rehearsal.get("integration_tree_sha")
    source_sha = rehearsal.get("source_sha")
    for name, value in (
        ("main_sha", main_sha),
        ("integration_tree_sha", integration_tree_sha),
        ("source_sha", source_sha),
    ):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
            raise IntegrationControlError(f"invalid Rehearsal {name}")

    main_ref = github_json(args.repo, "/git/ref/heads/main")
    current_main = (main_ref.get("object") or {}).get("sha")
    if current_main != main_sha:
        raise IntegrationControlError(
            f"current Rehearsal is stale: main={current_main} rehearsal={main_sha}"
        )

    branch = f"game-exp/integration/{issue}/{rehearsal_id}"
    return {
        "experiment_id": experiment_id,
        "issue_number": issue,
        "candidate_id": candidate_id,
        "rehearsal_id": rehearsal_id,
        "rehearsal_ref": rehearsal.get("rehearsal_ref"),
        "main_sha": main_sha,
        "source_sha": source_sha,
        "integration_tree_sha": integration_tree_sha,
        "branch": branch,
    }


def finalize(args: argparse.Namespace) -> dict[str, Any]:
    experiment_id = args.experiment_id
    match = re.fullmatch(r"EXP-([1-9][0-9]*)", experiment_id)
    if not match:
        raise IntegrationControlError("experiment_id must be EXP-<number>")
    issue = match.group(1)
    pr_number = str(args.pr_number)
    if not re.fullmatch(r"[1-9][0-9]*", pr_number):
        raise IntegrationControlError("pr_number must be a positive decimal string")

    state = github_content_json(
        args.repo,
        f"experiments/{experiment_id}/state.json",
        "game-exp/ledger",
    )
    if state.get("lifecycle") != "SELECTED":
        raise IntegrationControlError(
            f"Integration finalize requires SELECTED lifecycle, got {state.get('lifecycle')!r}"
        )
    candidate_id = state.get("current_candidate_id")
    rehearsal_id = state.get("current_rehearsal_id")
    if not isinstance(candidate_id, str) or not isinstance(rehearsal_id, str):
        raise IntegrationControlError("SELECTED experiment lacks current Candidate/Rehearsal")

    rehearsal = github_content_json(
        args.repo,
        f"experiments/{experiment_id}/rehearsals/{rehearsal_id}.json",
        "game-exp/ledger",
    )
    expected_branch = f"game-exp/integration/{issue}/{rehearsal_id}"
    pr = github_json(args.repo, f"/pulls/{urllib.parse.quote(pr_number, safe='')}")
    if pr.get("merged") is not True:
        raise IntegrationControlError("Integration PR is not merged")
    if (pr.get("base") or {}).get("ref") != "main":
        raise IntegrationControlError("Integration PR base must be main")
    head = pr.get("head") or {}
    if head.get("ref") != expected_branch:
        raise IntegrationControlError("Integration PR head ref is not canonical")
    if (head.get("repo") or {}).get("full_name") != args.repo:
        raise IntegrationControlError("Integration PR head repository mismatch")

    head_sha = head.get("sha")
    merge_sha = pr.get("merge_commit_sha")
    if not isinstance(head_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", head_sha):
        raise IntegrationControlError("Integration PR head SHA invalid")
    if not isinstance(merge_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", merge_sha):
        raise IntegrationControlError("Integration PR merge SHA invalid")

    head_commit = github_json(
        args.repo,
        f"/git/commits/{urllib.parse.quote(head_sha, safe='')}",
    )
    head_tree = (head_commit.get("tree") or {}).get("sha")
    head_parents = head_commit.get("parents")
    expected_tree = rehearsal.get("integration_tree_sha")
    expected_main = rehearsal.get("main_sha")
    if (
        head_tree != expected_tree
        or not isinstance(head_parents, list)
        or [row.get("sha") for row in head_parents if isinstance(row, dict)]
        != [expected_main]
    ):
        raise IntegrationControlError(
            "Integration PR head is not the exact single-parent Rehearsal tree"
        )

    merge_commit = github_json(
        args.repo,
        f"/git/commits/{urllib.parse.quote(merge_sha, safe='')}",
    )
    merge_tree = (merge_commit.get("tree") or {}).get("sha")
    if merge_tree != expected_tree:
        raise IntegrationControlError("merged PR tree differs from Rehearsal tree")

    compare = github_json(
        args.repo,
        f"/compare/{urllib.parse.quote(merge_sha, safe='')}...main",
    )
    merge_base = compare.get("merge_base_commit") or {}
    if compare.get("status") not in {"ahead", "identical"} or merge_base.get("sha") != merge_sha:
        raise IntegrationControlError("merged PR commit is not in current main history")

    merged_by = pr.get("merged_by") or {}
    merged_at = pr.get("merged_at")
    pr_id = pr.get("id")
    if (
        not isinstance(pr_id, int)
        or not isinstance(merged_at, str)
        or not isinstance(merged_by.get("login"), str)
        or not isinstance(merged_by.get("id"), int)
    ):
        raise IntegrationControlError("Integration PR merge identity is incomplete")

    integration_id = f"I-{issue}-PR-{pr_number}"
    return {
        "experiment_id": experiment_id,
        "candidate_id": candidate_id,
        "rehearsal_id": rehearsal_id,
        "integration_id": integration_id,
        "pr_number": pr_number,
        "pr_id": str(pr_id),
        "pr_url": str(pr.get("html_url") or ""),
        "head_ref": expected_branch,
        "head_sha": head_sha,
        "head_tree_sha": str(head_tree),
        "merge_sha": merge_sha,
        "merge_tree_sha": str(merge_tree),
        "merged_at": merged_at,
        "merged_by_login": merged_by["login"],
        "merged_by_user_id": str(merged_by["id"]),
        "workflow_source_sha": args.workflow_source_sha,
        "run_id": str(args.run_id),
        "run_attempt": str(args.run_attempt),
    }


def request_payload(args: argparse.Namespace) -> dict[str, Any]:
    context = json.loads(open(args.context_json, encoding="utf-8").read())
    if not isinstance(context, dict):
        raise IntegrationControlError("integration context must be an object")
    experiment_id = context.get("experiment_id")
    pr_number = context.get("pr_number")
    if not isinstance(experiment_id, str) or not isinstance(pr_number, str):
        raise IntegrationControlError("integration context missing experiment/pr identity")
    payload = build_operation_payload(
        "integration.register",
        {"experiment_id": experiment_id},
        preconditions={
            "candidate_id": context.get("candidate_id"),
            "rehearsal_id": context.get("rehearsal_id"),
            "pr_number": pr_number,
        },
    )
    request_id = f"req-integration-{experiment_id.removeprefix('EXP-')}-pr-{pr_number}"
    return {
        "request_id": request_id,
        "payload_b64": encode_payload_b64(payload),
        "payload": payload,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("prepare")
    p.add_argument("--repo", required=True)
    p.add_argument("--experiment-id", required=True)

    f = sub.add_parser("finalize")
    f.add_argument("--repo", required=True)
    f.add_argument("--experiment-id", required=True)
    f.add_argument("--pr-number", required=True)
    f.add_argument("--workflow-source-sha", required=True)
    f.add_argument("--run-id", required=True)
    f.add_argument("--run-attempt", required=True)

    q = sub.add_parser("payload")
    q.add_argument("--context-json", required=True)

    args = ap.parse_args()
    if args.command == "prepare":
        out = prepare(args)
    elif args.command == "finalize":
        out = finalize(args)
    else:
        out = request_payload(args)
    print(canonical_json_bytes(out).decode("utf-8"))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except IntegrationControlError as exc:
        print(json.dumps({"status": "INTEGRATION_CONTROL_ERROR", "error": str(exc)}, sort_keys=True))
        raise SystemExit(41)
