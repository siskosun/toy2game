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


class ArchiveControlError(RuntimeError):
    pass


ARCHIVE_MODES = {"ATOMIC_DELETE", "RETAIN_BRANCH"}


def github_json(repo: str, suffix: str, *, allow_404: bool = False) -> dict[str, Any] | None:
    token = os.environ.get("GAME_EXP_GITHUB_TOKEN")
    if not token:
        raise ArchiveControlError("GAME_EXP_GITHUB_TOKEN is required")
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}{suffix}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "game-exp-archive",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            value = json.load(response)
    except urllib.error.HTTPError as exc:
        if allow_404 and exc.code == 404:
            return None
        body = exc.read().decode("utf-8", errors="replace")[-1000:]
        raise ArchiveControlError(
            f"GitHub API failed ({exc.code}) for {suffix}: {body}"
        ) from exc
    if not isinstance(value, dict):
        raise ArchiveControlError(f"GitHub API returned non-object for {suffix}")
    return value


def github_content_json(repo: str, path: str, ref: str) -> dict[str, Any]:
    suffix = (
        "/contents/"
        + "/".join(urllib.parse.quote(part, safe="") for part in path.split("/"))
        + "?ref="
        + urllib.parse.quote(ref, safe="")
    )
    value = github_json(repo, suffix)
    encoded = value.get("content") if value else None
    if not isinstance(encoded, str):
        raise ArchiveControlError(f"missing content for {path}@{ref}")
    try:
        decoded = base64.b64decode(encoded).decode("utf-8")
        obj = json.loads(decoded)
    except Exception as exc:
        raise ArchiveControlError(f"invalid JSON for {path}@{ref}: {exc}") from exc
    if not isinstance(obj, dict):
        raise ArchiveControlError(f"{path}@{ref} must be an object")
    return obj


def _experiment_issue(experiment_id: str) -> str:
    match = re.fullmatch(r"EXP-([1-9][0-9]*)", experiment_id)
    if not match:
        raise ArchiveControlError("experiment_id must be EXP-<number>")
    return match.group(1)


def _archive_id(value: str) -> str:
    if not re.fullmatch(r"A-[1-9][0-9]*-[1-9][0-9]*", value):
        raise ArchiveControlError("archive_id must be A-<issue>-<sequence>")
    return value


def status(args: argparse.Namespace) -> dict[str, Any]:
    issue = _experiment_issue(args.experiment_id)
    state = github_content_json(
        args.repo,
        f"experiments/{args.experiment_id}/state.json",
        "game-exp/ledger",
    )
    lock = state.get("archive_lock")
    archive_id = None
    record = None
    if isinstance(lock, dict):
        archive_id = lock.get("archive_id")
    if archive_id is None and state.get("lifecycle") == "ARCHIVED":
        archive_id = state.get("current_archive_id")
    if archive_id is not None:
        if not isinstance(archive_id, str):
            raise ArchiveControlError("state archive identity is invalid")
        _archive_id(archive_id)
        record = github_content_json(
            args.repo,
            f"experiments/{args.experiment_id}/archives/{archive_id}.json",
            "game-exp/ledger",
        )
        if record.get("experiment_id") != args.experiment_id:
            raise ArchiveControlError("archive record experiment mismatch")
        if args.mode and record.get("mode") != args.mode:
            raise ArchiveControlError(
                f"existing archive mode {record.get('mode')!r} differs from requested {args.mode!r}"
            )

    return {
        "experiment_id": args.experiment_id,
        "issue_number": issue,
        "lifecycle": state.get("lifecycle"),
        "archive_lock": lock,
        "archive_id": archive_id,
        "archive": record,
        "needs_prepare": archive_id is None and state.get("lifecycle") != "ARCHIVED",
        "committed": (
            state.get("lifecycle") == "ARCHIVED"
            and isinstance(record, dict)
            and record.get("phase") == "COMMITTED"
        ),
    }


def request_payload(args: argparse.Namespace) -> dict[str, Any]:
    issue = _experiment_issue(args.experiment_id)
    operation = args.operation
    if operation == "prepare":
        if args.mode not in ARCHIVE_MODES:
            raise ArchiveControlError("prepare requires a valid archive mode")
        if not args.run_id or not args.run_attempt:
            raise ArchiveControlError("prepare requires run_id/run_attempt")
        payload = build_operation_payload(
            "archive.prepare",
            {"experiment_id": args.experiment_id, "mode": args.mode},
            actor_claim=args.actor_claim,
        )
        request_id = f"req-archive-prepare-{issue}-{args.run_id}-{args.run_attempt}"
    else:
        if not args.archive_id:
            raise ArchiveControlError(f"{operation} requires archive_id")
        archive_id = _archive_id(args.archive_id)
        if operation == "claim":
            payload = build_operation_payload(
                "archive.claim",
                {"experiment_id": args.experiment_id, "archive_id": archive_id},
            )
            request_id = f"req-archive-claim-{archive_id}"
        elif operation == "observe":
            if not args.run_id or not args.run_attempt:
                raise ArchiveControlError("observe requires run_id/run_attempt")
            payload = build_operation_payload(
                "archive.observe",
                {"experiment_id": args.experiment_id, "archive_id": archive_id},
            )
            request_id = (
                f"req-archive-observe-{archive_id}-{args.run_id}-{args.run_attempt}"
            )
        elif operation == "commit":
            payload = build_operation_payload(
                "archive.commit",
                {"experiment_id": args.experiment_id, "archive_id": archive_id},
            )
            request_id = f"req-archive-commit-{archive_id}"
        else:
            raise ArchiveControlError(f"unsupported archive operation {operation!r}")
    return {
        "request_id": request_id,
        "payload_b64": encode_payload_b64(payload),
        "payload": payload,
    }


def remote_state(args: argparse.Namespace) -> dict[str, Any]:
    _experiment_issue(args.experiment_id)
    archive_id = _archive_id(args.archive_id)
    record = github_content_json(
        args.repo,
        f"experiments/{args.experiment_id}/archives/{archive_id}.json",
        "game-exp/ledger",
    )
    mode = record.get("mode")
    expected = record.get("expected_branch_sha")
    branch_ref = record.get("branch_ref")
    final_tag_ref = record.get("final_tag_ref")
    if mode not in ARCHIVE_MODES:
        raise ArchiveControlError("archive record mode is invalid")
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{40}", expected):
        raise ArchiveControlError("archive expected branch SHA is invalid")
    if not isinstance(branch_ref, str) or not branch_ref.startswith("refs/heads/exp/"):
        raise ArchiveControlError("archive branch_ref is invalid")
    if not isinstance(final_tag_ref, str) or not final_tag_ref.startswith("refs/tags/exp-final/"):
        raise ArchiveControlError("archive final_tag_ref is invalid")

    branch_name = branch_ref.removeprefix("refs/heads/")
    tag_name = final_tag_ref.removeprefix("refs/tags/")
    branch_data = github_json(
        args.repo,
        "/git/ref/heads/" + "/".join(
            urllib.parse.quote(part, safe="") for part in branch_name.split("/")
        ),
        allow_404=True,
    )
    tag_data = github_json(
        args.repo,
        "/git/ref/tags/" + "/".join(
            urllib.parse.quote(part, safe="") for part in tag_name.split("/")
        ),
        allow_404=True,
    )
    branch_sha = None
    if branch_data is not None:
        obj = branch_data.get("object") or {}
        if obj.get("type") != "commit" or not isinstance(obj.get("sha"), str):
            raise ArchiveControlError("archive branch ref is not a commit ref")
        branch_sha = obj["sha"]

    final_target = None
    if tag_data is not None:
        obj = tag_data.get("object") or {}
        if obj.get("type") != "tag" or not isinstance(obj.get("sha"), str):
            raise ArchiveControlError("archive final ref is not an annotated tag")
        tag = github_json(
            args.repo,
            f"/git/tags/{urllib.parse.quote(obj['sha'], safe='')}",
        )
        target = tag.get("object") or {}
        if target.get("type") != "commit" or not isinstance(target.get("sha"), str):
            raise ArchiveControlError("archive final tag target is invalid")
        final_target = target["sha"]

    if mode == "ATOMIC_DELETE":
        if final_target == expected and branch_sha is None:
            classification = "REF_COMMITTED"
        elif final_target is None and branch_sha == expected:
            classification = "NOT_EXECUTED"
        else:
            classification = "REF_CONFLICT"
    else:
        if final_target == expected and branch_sha == expected:
            classification = "REF_COMMITTED"
        elif final_target is None and branch_sha == expected:
            classification = "NOT_EXECUTED"
        else:
            classification = "REF_CONFLICT"

    return {
        "experiment_id": args.experiment_id,
        "archive_id": archive_id,
        "phase": record.get("phase"),
        "mode": mode,
        "expected_branch_sha": expected,
        "branch_ref": branch_ref,
        "final_tag_ref": final_tag_ref,
        "branch_sha": branch_sha,
        "final_tag_target": final_target,
        "classification": classification,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command", required=True)

    s = sub.add_parser("status")
    s.add_argument("--repo", required=True)
    s.add_argument("--experiment-id", required=True)
    s.add_argument("--mode", choices=sorted(ARCHIVE_MODES))

    p = sub.add_parser("payload")
    p.add_argument("--operation", choices=("prepare", "claim", "observe", "commit"), required=True)
    p.add_argument("--experiment-id", required=True)
    p.add_argument("--archive-id")
    p.add_argument("--mode", choices=sorted(ARCHIVE_MODES))
    p.add_argument("--run-id")
    p.add_argument("--run-attempt")
    p.add_argument("--actor-claim")

    r = sub.add_parser("remote-state")
    r.add_argument("--repo", required=True)
    r.add_argument("--experiment-id", required=True)
    r.add_argument("--archive-id", required=True)

    args = ap.parse_args()
    if args.command == "status":
        out = status(args)
    elif args.command == "payload":
        out = request_payload(args)
    else:
        out = remote_state(args)
    print(canonical_json_bytes(out).decode("utf-8"))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ArchiveControlError as exc:
        print(
            json.dumps(
                {"status": "ARCHIVE_CONTROL_ERROR", "error": str(exc)},
                sort_keys=True,
            )
        )
        raise SystemExit(41)
