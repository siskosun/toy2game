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
from protocol_core import build_operation_payload, encode_payload_b64, validate_request_id


class BridgeError(RuntimeError):
    pass


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
    ]
    for key, value in fields.items():
        command.extend(["-f", f"{key}={value}"])
    env = os.environ.copy()
    env["GH_TOKEN"] = _token()
    proc = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        timeout=30,
    )
    if proc.returncode != 0:
        raise BridgeError(
            f"workflow dispatch failed ({proc.returncode}): {proc.stderr[-1200:]}"
        )
    url = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    return {
        "status": "ACCEPTED",
        "workflow": workflow,
        "workflow_url": url,
    }


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

    if action in WORKFLOW_ACTIONS:
        workflow, field_names = WORKFLOW_ACTIONS[action]
        fields = {name: str(command[name]) for name in field_names}
        return _dispatch_workflow(repo, workflow, fields)

    actor_claim = f"github-issue-comment:{actor_login}:{comment_id}"
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

    if action == "archive":
        proc = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("archive_runner.py")),
                "--repo",
                repo,
                "--experiment-id",
                command["experiment_id"],
                "--mode",
                command["mode"],
                "--ssh-key",
                ssh_key,
                "--run-id",
                run_id,
                "--run-attempt",
                run_attempt,
                "--workflow-source-sha",
                workflow_source_sha,
                "--actor-login",
                actor_login,
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=900,
        )
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        result = json.loads(lines[-1]) if lines else {}
        if proc.returncode != 0:
            raise BridgeError(
                f"archive bridge failed ({proc.returncode}): "
                + json.dumps(result, ensure_ascii=False)
                + proc.stderr[-1200:]
            )
        return result

    raise BridgeError(f"unhandled action {action!r}")


def _reply_body(
    request_id: str,
    result: dict[str, Any],
    *,
    phase: str = "result",
) -> str:
    payload = json.dumps(result, ensure_ascii=False, sort_keys=True)
    if len(payload) > 6000:
        payload = payload[:6000] + "...<truncated>"
    return "\n".join(
        [
            _marker(request_id, phase),
            f"game-exp bridge {phase} for {request_id}",
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
    validate_issue_binding(command, issue_number)
    permission = verify_actor(args.repo, actor_login)

    previous = find_marker_comment(
        args.repo, issue_number, request_id, "result"
    )
    if previous is not None:
        return {
            "status": "REPLAYED",
            "request_id": request_id,
            "permission": permission,
            "result_comment_id": previous.get("id"),
            "result_comment_url": previous.get("html_url"),
        }

    claim = find_marker_comment(args.repo, issue_number, request_id, "claim")
    if claim is not None:
        return {
            "status": "UNKNOWN",
            "request_id": request_id,
            "reason": "existing_claim_without_result",
            "claim_comment_id": claim.get("id"),
            "claim_comment_url": claim.get("html_url"),
        }

    claim_body = "\n".join(
        [
            _marker(request_id, "claim"),
            f"game-exp bridge claim for {request_id}",
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
    except Exception as exc:
        error = {
            "status": "REJECTED",
            "request_id": request_id,
            "error": str(exc),
            "claim_comment_id": claim_row.get("id"),
        }
        post_comment(args.repo, issue_number, _reply_body(request_id, error))
        raise

    result = {
        **result,
        "bridge_request_id": request_id,
        "bridge_actor": actor_login,
        "bridge_permission": permission,
        "claim_comment_id": claim_row.get("id"),
    }
    post_comment(args.repo, issue_number, _reply_body(request_id, result))
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
