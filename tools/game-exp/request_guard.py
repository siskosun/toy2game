from __future__ import annotations

import argparse
import json
import os
import re
import urllib.parse
import urllib.request
from typing import Any

from protocol_core import decode_payload_b64, digest_object, validate_request_id


class RequestGuardError(RuntimeError):
    pass


TITLE_RE = re.compile(
    r"^game-exp:request:([A-Za-z0-9][A-Za-z0-9_.-]{0,95}):"
    r"(sha256:[0-9a-f]{64}):([0-9a-f]{40})$"
)


def _token() -> str:
    token = os.environ.get("GAME_EXP_GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise RequestGuardError("GAME_EXP_GITHUB_TOKEN or GH_TOKEN is required")
    return token


def github_json(repo: str, suffix: str) -> Any:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}{suffix}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {_token()}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "game-exp-request-guard",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def decide_request_identity(
    *,
    repo: str,
    workflow: str,
    request_id: str,
    payload_b64: str,
    payload_digest: str,
    expected_head: str,
    run_id: str,
) -> dict[str, Any]:
    validate_request_id(request_id)
    if not re.fullmatch(r"[0-9a-f]{40}", expected_head):
        raise RequestGuardError("expected_head must be a 40-character SHA")
    if not run_id.isdigit():
        raise RequestGuardError("run_id must be a decimal string")
    payload = decode_payload_b64(payload_b64)
    actual_digest = digest_object(payload)
    if actual_digest != payload_digest:
        raise RequestGuardError(
            f"payload digest mismatch: expected {payload_digest} actual {actual_digest}"
        )

    encoded = urllib.parse.quote(workflow, safe="")
    prefix = f"game-exp:request:{request_id}:"
    matches: list[dict[str, Any]] = []
    for page in range(1, 11):
        value = github_json(
            repo,
            f"/actions/workflows/{encoded}/runs?event=workflow_dispatch&per_page=100&page={page}",
        )
        rows = value.get("workflow_runs") if isinstance(value, dict) else None
        if not isinstance(rows, list):
            raise RequestGuardError("workflow runs response is invalid")
        for row in rows:
            if not isinstance(row, dict):
                continue
            title = row.get("display_title")
            if isinstance(title, str) and title.startswith(prefix):
                match = TITLE_RE.fullmatch(title)
                if match is not None and match.group(1) == request_id:
                    matches.append(
                        {
                            "id": row.get("id"),
                            "digest": match.group(2),
                            "expected_head": match.group(3),
                            "title": title,
                        }
                    )
        if len(rows) < 100:
            break

    valid = [
        row
        for row in matches
        if isinstance(row.get("id"), int)
    ]
    valid.sort(key=lambda row: int(row["id"]))
    current = int(run_id)
    current_rows = [row for row in valid if row["id"] == current]
    if not current_rows:
        raise RequestGuardError(
            f"current workflow run {current} was not discoverable for request {request_id}"
        )
    first = valid[0]
    if first["digest"] != payload_digest or first["expected_head"] != expected_head:
        return {
            "status": "CONFLICT",
            "conflict_type": "REQUEST_ID_CONFLICT",
            "request_id": request_id,
            "run_id": run_id,
            "first_run_id": str(first["id"]),
            "first_payload_digest": first["digest"],
            "new_payload_digest": payload_digest,
            "first_expected_head": first["expected_head"],
            "new_expected_head": expected_head,
        }
    return {
        "status": "PASS",
        "request_id": request_id,
        "run_id": run_id,
        "first_run_id": str(first["id"]),
        "payload_digest": payload_digest,
        "expected_head": expected_head,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--payload-b64", required=True)
    parser.add_argument("--payload-digest", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--github-output")
    args = parser.parse_args()

    result = decide_request_identity(
        repo=args.repo,
        workflow=args.workflow,
        request_id=args.request_id,
        payload_b64=args.payload_b64,
        payload_digest=args.payload_digest,
        expected_head=args.expected_head,
        run_id=args.run_id,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as output:
            output.write(f"status={result['status']}\n")
            output.write(
                f"conflict_type={result.get('conflict_type', '')}\n"
            )
            output.write(
                f"first_payload_digest={result.get('first_payload_digest', result.get('payload_digest', ''))}\n"
            )
    return 43 if result["status"] == "CONFLICT" else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            json.dumps(
                {"status": "REQUEST_GUARD_ERROR", "error": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        raise SystemExit(41)
