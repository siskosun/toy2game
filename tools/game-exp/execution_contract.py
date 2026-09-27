from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from protocol_core import ASYNC_EXECUTION_ACTIONS, digest_object, validate_request_id


class ExecutionContractError(RuntimeError):
    pass


def _token() -> str:
    token = os.environ.get("GAME_EXP_GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise ExecutionContractError("GAME_EXP_GITHUB_TOKEN or GH_TOKEN is required")
    return token


def github_json(repo: str, path: str, *, ref: str = "game-exp/ledger") -> dict[str, Any]:
    encoded_path = "/".join(urllib.parse.quote(part, safe="") for part in path.split("/"))
    query = urllib.parse.urlencode({"ref": ref})
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/contents/{encoded_path}?{query}",
        headers={
            "Accept": "application/vnd.github.raw+json",
            "Authorization": f"Bearer {_token()}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "game-exp-execution-contract",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[-1200:]
        raise ExecutionContractError(
            f"cannot read {path} from protected Ledger: HTTP {exc.code} {detail}"
        ) from exc
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ExecutionContractError(f"Ledger object {path} must be an object")
    return value


def verify_claim(
    *,
    repo: str,
    request_id: str,
    action: str,
    experiment_id: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    validate_request_id(request_id)
    if action not in ASYNC_EXECUTION_ACTIONS:
        raise ExecutionContractError(f"unsupported execution action {action!r}")
    if not re.fullmatch(r"EXP-[1-9][0-9]*", experiment_id):
        raise ExecutionContractError("experiment_id must be EXP-<positive integer>")

    operation = github_json(repo, f"operations/{request_id}.json")
    execution = github_json(repo, f"executions/{request_id}.json")
    state = github_json(repo, f"experiments/{experiment_id}/state.json")

    payload = operation.get("payload")
    if (
        operation.get("request_id") != request_id
        or operation.get("domain_status") != "APPLIED"
        or operation.get("domain_experiment_id") != experiment_id
        or not isinstance(payload, dict)
        or payload.get("kind") != "operation_request"
        or payload.get("operation") != "execution.claim"
    ):
        raise ExecutionContractError("execution claim operation is not authoritatively applied")

    claim_input = payload.get("input")
    expected_input = {
        "experiment_id": experiment_id,
        "action": action,
        "arguments": arguments,
        "state_digest": execution.get("state_digest"),
    }
    if not isinstance(claim_input, dict) or claim_input != expected_input:
        raise ExecutionContractError(
            "workflow inputs do not match the authoritative execution claim"
        )

    if (
        execution.get("kind") != "execution_claim"
        or execution.get("request_id") != request_id
        or execution.get("experiment_id") != experiment_id
        or execution.get("action") != action
        or execution.get("arguments") != arguments
        or execution.get("phase") != "CLAIMED"
    ):
        raise ExecutionContractError("execution claim projection is invalid")

    actor = execution.get("actor")
    if (
        not isinstance(actor, dict)
        or not isinstance(actor.get("login"), str)
        or actor.get("permission_at_claim") not in {"admin", "maintain", "write"}
    ):
        raise ExecutionContractError("execution claim lacks trusted write-authorized actor")

    current_state_digest = digest_object(state)
    claimed_state_digest = execution.get("state_digest")
    if current_state_digest != claimed_state_digest:
        raise ExecutionContractError(
            "experiment state changed after execution claim; recover the original operation "
            "instead of re-running it against a new state"
        )

    return {
        "status": "PASS",
        "request_id": request_id,
        "experiment_id": experiment_id,
        "action": action,
        "arguments": arguments,
        "state_digest": claimed_state_digest,
        "actor": actor,
        "operation_payload_digest": operation.get("payload_digest"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--request-id", required=True)
    ap.add_argument("--action", required=True, choices=ASYNC_EXECUTION_ACTIONS)
    ap.add_argument("--experiment-id", required=True)
    ap.add_argument("--arguments-json", default="{}")
    args = ap.parse_args()
    try:
        arguments = json.loads(args.arguments_json)
    except json.JSONDecodeError as exc:
        raise ExecutionContractError(f"invalid --arguments-json: {exc}") from exc
    if not isinstance(arguments, dict):
        raise ExecutionContractError("--arguments-json must decode to an object")
    result = verify_claim(
        repo=args.repo,
        request_id=args.request_id,
        action=args.action,
        experiment_id=args.experiment_id,
        arguments=arguments,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ExecutionContractError as exc:
        print(
            json.dumps(
                {"status": "EXECUTION_CONTRACT_ERROR", "error": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        raise SystemExit(41)
