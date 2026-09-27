from __future__ import annotations

import argparse
import json
import os
import urllib.parse
import urllib.request
from typing import Any

from protocol_core import digest_object, validate_request_id


class WorkflowGuardError(RuntimeError):
    pass


def _token() -> str:
    token = os.environ.get("GAME_EXP_GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise WorkflowGuardError("GAME_EXP_GITHUB_TOKEN or GH_TOKEN is required")
    return token


def github_json(repo: str, suffix: str, *, raw: bool = False) -> Any:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}{suffix}",
        headers={
            "Accept": (
                "application/vnd.github.raw+json"
                if raw
                else "application/vnd.github+json"
            ),
            "Authorization": f"Bearer {_token()}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "game-exp-workflow-guard",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def validate_execution_claim(
    *,
    repo: str,
    action: str,
    experiment_id: str,
    request_id: str,
    arguments: dict[str, str],
) -> dict[str, Any]:
    validate_request_id(request_id)
    encoded_request = urllib.parse.quote(request_id, safe="")
    record = github_json(
        repo,
        f"/contents/operations/{encoded_request}.json?ref=game-exp%2Fledger",
        raw=True,
    )
    if not isinstance(record, dict):
        raise WorkflowGuardError("execution claim record is not an object")
    payload = record.get("payload")
    if not isinstance(payload, dict):
        raise WorkflowGuardError("execution claim payload is missing")
    if record.get("payload_digest") != digest_object(payload):
        raise WorkflowGuardError("execution claim payload digest mismatch")
    if record.get("domain_status") != "REQUEST_ONLY":
        raise WorkflowGuardError("execution claim domain status is invalid")
    if payload.get("kind") != "operation_request" or payload.get("operation") != "execution.claim":
        raise WorkflowGuardError("request id is not an execution claim")
    claim = payload.get("input")
    if not isinstance(claim, dict):
        raise WorkflowGuardError("execution claim input is invalid")
    if (
        claim.get("action") != action
        or claim.get("experiment_id") != experiment_id
        or claim.get("arguments") != arguments
    ):
        raise WorkflowGuardError("workflow invocation differs from trusted execution claim")
    actor = record.get("trusted_actor")
    if not isinstance(actor, dict):
        raise WorkflowGuardError("execution claim has no trusted actor")
    if actor.get("permission") not in {"admin", "maintain", "write"}:
        raise WorkflowGuardError("execution claim actor lacks write permission")
    login = actor.get("login")
    if not isinstance(login, str) or not login:
        raise WorkflowGuardError("execution claim actor login is invalid")
    return {
        "actor_login": login,
        "actor_user_id": actor.get("user_id"),
        "actor_permission": actor.get("permission"),
        "state_digest": claim.get("state_digest"),
    }


def expected_title(action: str, request_id: str) -> str:
    validate_request_id(request_id)
    return f"game-exp:{action}:{request_id}"


def decide_primary(
    *,
    repo: str,
    workflow: str,
    action: str,
    request_id: str,
    run_id: str,
    experiment_id: str,
    arguments: dict[str, str],
) -> dict[str, Any]:
    validate_request_id(request_id)
    if not run_id.isdigit():
        raise WorkflowGuardError("run_id must be a decimal string")
    title = expected_title(action, request_id)
    claim = validate_execution_claim(
        repo=repo,
        action=action,
        experiment_id=experiment_id,
        request_id=request_id,
        arguments=arguments,
    )
    matches: list[dict[str, Any]] = []
    encoded = urllib.parse.quote(workflow, safe="")
    for page in range(1, 11):
        value = github_json(
            repo,
            f"/actions/workflows/{encoded}/runs?event=workflow_dispatch&per_page=100&page={page}",
        )
        rows = value.get("workflow_runs") if isinstance(value, dict) else None
        if not isinstance(rows, list):
            raise WorkflowGuardError("workflow runs response is invalid")
        for row in rows:
            if isinstance(row, dict) and row.get("display_title") == title:
                matches.append(row)
        if len(rows) < 100:
            break

    ids = sorted(
        int(row["id"])
        for row in matches
        if isinstance(row.get("id"), int)
    )
    current = int(run_id)
    if current not in ids:
        raise WorkflowGuardError(
            f"current workflow run {current} was not discoverable for {title}"
        )
    primary = ids[0]
    return {
        "status": "PASS",
        "action": action,
        "request_id": request_id,
        "display_title": title,
        "run_id": run_id,
        "primary_run_id": str(primary),
        "primary": current == primary,
        "duplicate_run_ids": [str(value) for value in ids[1:]],
        "trusted_actor": claim,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--action", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--argument", action="append", default=[])
    parser.add_argument("--github-output")
    args = parser.parse_args()

    arguments: dict[str, str] = {}
    for raw in args.argument:
        if "=" not in raw:
            raise WorkflowGuardError("--argument must use name=value")
        key, value = raw.split("=", 1)
        if not key or key in arguments:
            raise WorkflowGuardError("invalid or duplicate workflow argument")
        arguments[key] = value
    result = decide_primary(
        repo=args.repo,
        workflow=args.workflow,
        action=args.action,
        request_id=args.request_id,
        run_id=args.run_id,
        experiment_id=args.experiment_id,
        arguments=arguments,
    )
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as output:
            output.write(f"primary={'true' if result['primary'] else 'false'}\n")
            output.write(f"primary_run_id={result['primary_run_id']}\n")
            output.write(
                f"actor_login={result['trusted_actor']['actor_login']}\n"
            )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            json.dumps(
                {"status": "WORKFLOW_GUARD_ERROR", "error": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        raise SystemExit(41)
