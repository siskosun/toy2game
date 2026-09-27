from __future__ import annotations

import argparse
import json
import os
import urllib.parse
import urllib.request
from typing import Any

from protocol_core import validate_request_id


class WorkflowGuardError(RuntimeError):
    pass


def _token() -> str:
    token = os.environ.get("GAME_EXP_GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise WorkflowGuardError("GAME_EXP_GITHUB_TOKEN or GH_TOKEN is required")
    return token


def github_json(repo: str, suffix: str) -> Any:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}{suffix}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {_token()}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "game-exp-workflow-guard",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


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
) -> dict[str, Any]:
    validate_request_id(request_id)
    if not run_id.isdigit():
        raise WorkflowGuardError("run_id must be a decimal string")
    title = expected_title(action, request_id)
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
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--action", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--github-output")
    args = parser.parse_args()

    result = decide_primary(
        repo=args.repo,
        workflow=args.workflow,
        action=args.action,
        request_id=args.request_id,
        run_id=args.run_id,
    )
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as output:
            output.write(f"primary={'true' if result['primary'] else 'false'}\n")
            output.write(f"primary_run_id={result['primary_run_id']}\n")
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
