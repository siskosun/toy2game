from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import archive_runner
from client import GameExpClient, GitHubTransport
from protocol_core import build_operation_payload, digest_object, encode_payload_b64, validate_request_id


class BridgeError(RuntimeError):
    pass


class BridgeUncertainError(BridgeError):
    """The async worker may have been dispatched, but the Bridge cannot prove it."""


WRITE_PERMISSIONS = {"admin", "maintain", "write", "push"}
BOT_LOGIN = "github-actions[bot]"
COMMAND_PREFIX = "/game-exp"

WORKFLOW_ACTIONS = {
    "initialize": ("game-exp-source-initializer.yml", ("experiment_id",)),
    "candidate_build": ("game-exp-candidate.yml", ("experiment_id",)),
    "rehearse": ("game-exp-rehearsal.yml", ("experiment_id",)),
    "integrate": ("game-exp-integration.yml", ("experiment_id",)),
    "integrate_finalize": (
        "game-exp-integration-finalize.yml",
        ("experiment_id", "pr_number"),
    ),
    "archive": ("game-exp-archive.yml", ("experiment_id", "mode")),
}

ACTION_KEYS = {
    "status": {"schema_version", "request_id", "action", "experiment_id"},
    "bind": {"schema_version", "request_id", "action", "manifest"},
    "initialize": {"schema_version", "request_id", "action", "experiment_id"},
    "candidate_build": {"schema_version", "request_id", "action", "experiment_id"},
    "review_record": {
        "schema_version",
        "request_id",
        "action",
        "experiment_id",
        "candidate_id",
        "outcome",
        "notes",
    },
    "decision_submit": {
        "schema_version",
        "request_id",
        "action",
        "experiment_id",
        "to_state",
        "reason",
        "previous_decision_id",
    },
    "rehearse": {"schema_version", "request_id", "action", "experiment_id"},
    "integrate": {"schema_version", "request_id", "action", "experiment_id"},
    "integrate_finalize": {
        "schema_version",
        "request_id",
        "action",
        "experiment_id",
        "pr_number",
    },
    "archive": {
        "schema_version",
        "request_id",
        "action",
        "experiment_id",
        "mode",
    },
    "archive_abort": {
        "schema_version",
        "request_id",
        "action",
        "experiment_id",
        "archive_id",
        "reason",
    },
}


def _token() -> str:
    token = os.environ.get("GAME_EXP_GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise BridgeError("GAME_EXP_GITHUB_TOKEN or GH_TOKEN is required")
    return token


def github_json(
    repo: str,
    suffix: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
) -> Any:
    data = None
    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}{suffix}",
        method=method,
        data=data,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {_token()}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "game-exp-github-bridge",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[-1200:]
        raise BridgeError(f"GitHub API failed ({exc.code}) for {suffix}: {detail}") from exc
    if not raw:
        return None
    return json.loads(raw.decode("utf-8"))


def parse_command(body: str) -> dict[str, Any]:
    lines = body.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != COMMAND_PREFIX:
        raise BridgeError("comment must start with /game-exp")
    raw = "\n".join(lines[1:]).strip()
    if raw.startswith("~~~") and raw.endswith("~~~"):
        block = raw.splitlines()
        if len(block) < 3:
            raise BridgeError("fenced command JSON is incomplete")
        raw = "\n".join(block[1:-1]).strip()
        if raw.startswith("json\n"):
            raw = raw[5:].lstrip()
    try:
        command = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BridgeError(f"command body is not valid JSON: {exc}") from exc
    if not isinstance(command, dict):
        raise BridgeError("command JSON must be an object")
    if command.get("schema_version") != 1:
        raise BridgeError("schema_version must equal 1")
    action = command.get("action")
    if action not in ACTION_KEYS:
        raise BridgeError(f"unsupported action: {action!r}")
    if set(command) != ACTION_KEYS[action]:
        missing = ACTION_KEYS[action] - set(command)
        extra = set(command) - ACTION_KEYS[action]
        raise BridgeError(f"command keys mismatch; missing={sorted(missing)} extra={sorted(extra)}")
    request_id = command.get("request_id")
    if not isinstance(request_id, str):
        raise BridgeError("request_id must be a string")
    validate_request_id(request_id)
    return command


def _issue_from_experiment(experiment_id: str) -> str:
    match = re.fullmatch(r"EXP-([1-9][0-9]*)", experiment_id)
    if not match:
        raise BridgeError("experiment_id must be EXP-<positive integer>")
    return match.group(1)


def validate_issue_binding(command: dict[str, Any], issue_number: str) -> None:
    action = command["action"]
    if action == "bind":
        manifest = command["manifest"]
        if not isinstance(manifest, dict):
            raise BridgeError("manifest must be an object")
        exp = manifest.get("experiment")
        if not isinstance(exp, dict) or str(exp.get("issue_number")) != issue_number:
            raise BridgeError("manifest issue_number must match the comment Issue")
        if manifest.get("operation_id") != command["request_id"]:
            raise BridgeError("bind request_id must equal manifest.operation_id")
        return
    experiment_id = command.get("experiment_id")
    if not isinstance(experiment_id, str):
        raise BridgeError("experiment_id is required")
    if _issue_from_experiment(experiment_id) != issue_number:
        raise BridgeError("experiment_id must match the comment Issue number")


def verify_actor(repo: str, actor_login: str) -> str:
    value = github_json(
        repo,
        "/collaborators/" + urllib.parse.quote(actor_login, safe="") + "/permission",
    )
    permission = value.get("permission") if isinstance(value, dict) else None
    user = value.get("user") if isinstance(value, dict) else None
    if not isinstance(user, dict) or user.get("login") != actor_login:
        raise BridgeError("trusted collaborator identity mismatch")
    if permission not in WRITE_PERMISSIONS:
        raise BridgeError(
            f"actor {actor_login!r} lacks write permission required for game-exp bridge"
        )
    return str(permission)


def _comments(repo: str, issue_number: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    page = 1
    while page <= 10:
        value = github_json(
            repo,
            f"/issues/{issue_number}/comments?per_page=100&page={page}",
        )
        if not isinstance(value, list):
            raise BridgeError("issue comments response must be a list")
        rows.extend(row for row in value if isinstance(row, dict))
        if len(value) < 100:
            break
        page += 1
    return rows


def _marker(request_id: str, phase: str) -> str:
    return f"<!-- game-exp-bridge:{request_id}:{phase} -->"


def _command_digest_line(command_digest: str) -> str:
    return f"game-exp-command-digest: {command_digest}"


def _comment_command_digest(row: dict[str, Any]) -> str | None:
    body = row.get("body")
    if not isinstance(body, str):
        return None
    match = re.search(
        r"(?m)^game-exp-command-digest: (sha256:[0-9a-f]{64})$",
        body,
    )
    return match.group(1) if match else None


def _marker_replay_status(
    row: dict[str, Any],
    *,
    request_id: str,
    command_digest: str,
    phase: str,
) -> dict[str, Any] | None:
    stored = _comment_command_digest(row)
    if stored is None:
        return {
            "status": "UNKNOWN",
            "request_id": request_id,
            "reason": "legacy_marker_without_command_digest",
            f"{phase}_comment_id": row.get("id"),
            f"{phase}_comment_url": row.get("html_url"),
        }
    if stored != command_digest:
        return {
            "status": "CONFLICT",
            "conflict_type": "REQUEST_ID_CONFLICT",
            "request_id": request_id,
            "expected_command_digest": stored,
            "new_command_digest": command_digest,
            f"{phase}_comment_id": row.get("id"),
            f"{phase}_comment_url": row.get("html_url"),
        }
    return None


def find_marker_comment(
    repo: str,
    issue_number: str,
    request_id: str,
    phase: str,
) -> dict[str, Any] | None:
    marker = _marker(request_id, phase)
    for row in reversed(_comments(repo, issue_number)):
        body = row.get("body")
        user = row.get("user") or {}
        if (
            isinstance(body, str)
            and marker in body
            and user.get("login") == BOT_LOGIN
        ):
            return row
    return None


def post_comment(repo: str, issue_number: str, body: str) -> dict[str, Any]:
    value = github_json(
        repo,
        f"/issues/{issue_number}/comments",
        method="POST",
        body={"body": body},
    )
    if not isinstance(value, dict) or not isinstance(value.get("id"), int):
        raise BridgeError("failed to create bridge Issue comment")
    return value


def submit_writer(
    repo: str,
    request_id: str,
    payload: dict[str, Any],
    *,
    ssh_key: str,
    run_id: str,
    run_attempt: str,
    workflow_source_sha: str,
) -> dict[str, Any]:
    info = {
        "request_id": request_id,
        "payload_b64": encode_payload_b64(payload),
    }
    return archive_runner.submit_writer(
        repo=repo,
        info=info,
        authority="request",
        ssh_key=ssh_key,
        run_id=run_id,
        run_attempt=run_attempt,
        workflow_source_sha=workflow_source_sha,
        writer_path=str(Path(__file__).with_name("trusted_writer.py")),
    )


def _dispatch_workflow(
    repo: str,
    workflow: str,
    fields: dict[str, str],
    *,
    request_id: str,
) -> dict[str, Any]:
    command = [
        "gh",
        "workflow",
        "run",
        workflow,
        "--repo",
        repo,
        "--ref",
        "main",
        "-f",
        f"request_id={request_id}",
    ]
    for key, value in fields.items():
        command.extend(["-f", f"{key}={value}"])
    env = os.environ.copy()
    env["GH_TOKEN"] = _token()
    try:
        proc = subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            errors="strict",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise BridgeUncertainError(
            "workflow dispatch timed out; recover the same request_id"
        ) from exc
    if proc.returncode != 0:
        raise BridgeUncertainError(
            f"workflow dispatch outcome is uncertain ({proc.returncode}): "
            f"{proc.stderr[-1200:]}"
        )
    url = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    if not url:
        raise BridgeUncertainError(
            "workflow dispatch returned no run URL; recover the same request_id"
        )
    return {
        "status": "ACCEPTED",
        "workflow": workflow,
        "workflow_url": url,
    }


def _claim_async_execution(
    command: dict[str, Any],
    *,
    repo: str,
    actor_login: str,
    comment_id: str,
    ssh_key: str,
    run_id: str,
    run_attempt: str,
    workflow_source_sha: str,
) -> dict[str, Any]:
    action = str(command["action"])
    experiment_id = str(command["experiment_id"])
    transport = GitHubTransport(repo)
    head = transport.ledger_head()
    state = transport.ledger_json(
        f"experiments/{experiment_id}/state.json",
        ref=head,
    )
    if not isinstance(state, dict):
        raise BridgeError("experiment state is unavailable for execution claim")
    arguments = {
        key: command[key]
        for key in ("pr_number", "mode")
        if key in command
    }
    payload = build_operation_payload(
        "execution.claim",
        {
            "experiment_id": experiment_id,
            "action": action,
            "arguments": arguments,
            "state_digest": digest_object(state),
        },
    )
    result = submit_writer(
        repo,
        command["request_id"],
        payload,
        ssh_key=ssh_key,
        run_id=run_id,
        run_attempt=run_attempt,
        workflow_source_sha=workflow_source_sha,
    )
    return result


def execute_action(
    command: dict[str, Any],
    *,
    repo: str,
    actor_login: str,
    comment_id: str,
    ssh_key: str,
    run_id: str,
    run_attempt: str,
    workflow_source_sha: str,
) -> dict[str, Any]:
    action = command["action"]
    experiment_id = command.get("experiment_id")

    if action == "status":
        return GameExpClient(GitHubTransport(repo)).experiment_get(str(experiment_id))

    actor_claim = f"github-issue-comment:{actor_login}:{comment_id}"

    if action in WORKFLOW_ACTIONS:
        claim_result = _claim_async_execution(
            command,
            repo=repo,
            actor_login=actor_login,
            comment_id=comment_id,
            ssh_key=ssh_key,
            run_id=run_id,
            run_attempt=run_attempt,
            workflow_source_sha=workflow_source_sha,
        )
        if claim_result.get("status") != "COMMITTED":
            return {
                **claim_result,
                "operation_status": "CLAIM_NOT_COMMITTED",
                "recovery": "resolve the same request_id; do not create a replacement operation",
            }
        workflow, field_names = WORKFLOW_ACTIONS[action]
        fields = {name: str(command[name]) for name in field_names}
        return _dispatch_workflow(
            repo,
            workflow,
            fields,
            request_id=command["request_id"],
        )

    if action == "bind":
        payload = build_operation_payload(
            "experiment.bind",
            {"manifest": command["manifest"]},
            actor_claim=actor_claim,
        )
        return submit_writer(
            repo,
            command["request_id"],
            payload,
            ssh_key=ssh_key,
            run_id=run_id,
            run_attempt=run_attempt,
            workflow_source_sha=workflow_source_sha,
        )

    if action == "review_record":
        payload = build_operation_payload(
            "review.record",
            {
                "experiment_id": command["experiment_id"],
                "candidate_id": command["candidate_id"],
                "outcome": command["outcome"],
                "notes": command["notes"],
            },
            actor_claim=actor_claim,
        )
        return submit_writer(
            repo,
            command["request_id"],
            payload,
            ssh_key=ssh_key,
            run_id=run_id,
            run_attempt=run_attempt,
            workflow_source_sha=workflow_source_sha,
        )

    if action == "decision_submit":
        payload = build_operation_payload(
            "experiment.decision",
            {
                "experiment_id": command["experiment_id"],
                "to_state": command["to_state"],
                "previous_decision_id": command["previous_decision_id"],
                "reason": command["reason"],
            },
            actor_claim=actor_claim,
        )
        return submit_writer(
            repo,
            command["request_id"],
            payload,
            ssh_key=ssh_key,
            run_id=run_id,
            run_attempt=run_attempt,
            workflow_source_sha=workflow_source_sha,
        )

    if action == "archive_abort":
        payload = build_operation_payload(
            "archive.abort",
            {
                "experiment_id": command["experiment_id"],
                "archive_id": command["archive_id"],
                "reason": command["reason"],
            },
            actor_claim=actor_claim,
        )
        return submit_writer(
            repo,
            command["request_id"],
            payload,
            ssh_key=ssh_key,
            run_id=run_id,
            run_attempt=run_attempt,
            workflow_source_sha=workflow_source_sha,
        )

    raise BridgeError(f"unhandled action {action!r}")


def _reply_body(
    request_id: str,
    result: dict[str, Any],
    *,
    phase: str = "result",
    command_digest: str | None = None,
) -> str:
    payload = json.dumps(result, ensure_ascii=False, sort_keys=True)
    if len(payload) > 6000:
        payload = payload[:6000] + "...<truncated>"
    return "\n".join(
        [
            _marker(request_id, phase),
            f"game-exp bridge {phase} for {request_id}",
            *(
                [_command_digest_line(command_digest)]
                if command_digest is not None
                else []
            ),
            "",
            "~~~json",
            payload,
            "~~~",
        ]
    )


def run_event(args: argparse.Namespace) -> dict[str, Any]:
    event = json.loads(Path(args.event_path).read_text(encoding="utf-8"))
    comment = event.get("comment") or {}
    issue = event.get("issue") or {}
    sender = event.get("sender") or {}
    if "pull_request" in issue:
        raise BridgeError("game-exp bridge accepts Issue comments, not PR comments")
    body = comment.get("body")
    comment_user = (comment.get("user") or {}).get("login")
    actor_login = sender.get("login")
    issue_number = str(issue.get("number"))
    comment_id = str(comment.get("id"))
    if not isinstance(body, str):
        raise BridgeError("Issue comment body is unavailable")
    if comment_user != actor_login or not isinstance(actor_login, str):
        raise BridgeError("Issue comment actor identity mismatch")

    command = parse_command(body)
    request_id = command["request_id"]
    command_digest = digest_object(command)
    validate_issue_binding(command, issue_number)
    permission = verify_actor(args.repo, actor_login)

    previous = find_marker_comment(
        args.repo, issue_number, request_id, "result"
    )
    if previous is not None:
        marker_status = _marker_replay_status(
            previous,
            request_id=request_id,
            command_digest=command_digest,
            phase="result",
        )
        if marker_status is not None:
            return marker_status
        return {
            "status": "REPLAYED",
            "request_id": request_id,
            "permission": permission,
            "command_digest": command_digest,
            "result_comment_id": previous.get("id"),
            "result_comment_url": previous.get("html_url"),
        }

    claim = find_marker_comment(args.repo, issue_number, request_id, "claim")
    if claim is not None:
        marker_status = _marker_replay_status(
            claim,
            request_id=request_id,
            command_digest=command_digest,
            phase="claim",
        )
        if marker_status is not None:
            return marker_status
        return {
            "status": "UNKNOWN",
            "request_id": request_id,
            "reason": "existing_claim_without_result",
            "command_digest": command_digest,
            "claim_comment_id": claim.get("id"),
            "claim_comment_url": claim.get("html_url"),
        }

    claim_body = "\n".join(
        [
            _marker(request_id, "claim"),
            f"game-exp bridge claim for {request_id}",
            _command_digest_line(command_digest),
            f"Bridge run: https://github.com/{args.repo}/actions/runs/{args.run_id}",
        ]
    )
    claim_row = post_comment(args.repo, issue_number, claim_body)

    try:
        result = execute_action(
            command,
            repo=args.repo,
            actor_login=actor_login,
            comment_id=comment_id,
            ssh_key=args.ssh_key,
            run_id=args.run_id,
            run_attempt=args.run_attempt,
            workflow_source_sha=args.workflow_source_sha,
        )
    except BridgeUncertainError as exc:
        error = {
            "status": "UNKNOWN",
            "request_id": request_id,
            "reason": "worker_dispatch_outcome_uncertain",
            "error": str(exc),
            "claim_comment_id": claim_row.get("id"),
            "recovery": "query/resume this same request_id; do not submit a new logical operation",
        }
        post_comment(
            args.repo,
            issue_number,
            _reply_body(request_id, error, command_digest=command_digest),
        )
        return error
    except Exception as exc:
        error = {
            "status": "REJECTED",
            "request_id": request_id,
            "error": str(exc),
            "claim_comment_id": claim_row.get("id"),
        }
        post_comment(
            args.repo,
            issue_number,
            _reply_body(request_id, error, command_digest=command_digest),
        )
        raise

    result = {
        **result,
        "bridge_request_id": request_id,
        "bridge_actor": actor_login,
        "bridge_permission": permission,
        "claim_comment_id": claim_row.get("id"),
    }
    result["bridge_command_digest"] = command_digest
    post_comment(
        args.repo,
        issue_number,
        _reply_body(request_id, result, command_digest=command_digest),
    )
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--event-path", required=True)
    ap.add_argument("--ssh-key", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--run-attempt", required=True)
    ap.add_argument("--workflow-source-sha", required=True)
    args = ap.parse_args()
    result = run_event(args)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            json.dumps(
                {"status": "BRIDGE_ERROR", "error": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        raise SystemExit(41)
