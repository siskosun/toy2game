from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from client import ClientError, GameExpClient, GitHubTransport
from protocol_core import ProtocolError, strict_json_loads


def _load_object(value: str | None, path: str | None, *, label: str) -> dict[str, Any]:
    if value is not None and path is not None:
        raise ProtocolError(f"use only one of --{label} or --{label}-file")
    if path is not None:
        text = Path(path).read_text(encoding="utf-8")
    elif value is not None:
        text = value
    else:
        return {}
    obj = strict_json_loads(text)
    if not isinstance(obj, dict):
        raise ProtocolError(f"{label} must be a JSON object")
    return obj


def _print_result(result: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return

    status = result.get("status", "UNKNOWN")
    print(f"status: {status}")
    if result.get("request_id"):
        print(f"request_id: {result['request_id']}")
    if result.get("repo"):
        print(f"repo: {result['repo']}")
    if result.get("workflow_url"):
        print(f"workflow: {result['workflow_url']}")
    if result.get("ledger_head"):
        print(f"ledger_head: {result['ledger_head']}")
    if result.get("conflict_type"):
        print(f"conflict: {result['conflict_type']}")
    if result.get("reason"):
        print(f"reason: {result['reason']}")
    if "checks" in result:
        for check in result["checks"]:
            detail = check.get("detail")
            suffix = "" if detail in (None, "", {}) else f" — {detail}"
            print(f"{check['status']:7} {check['name']}{suffix}")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="game-exp",
        description="Trusted request client for the game-exp protocol.",
    )
    ap.add_argument("--repo", help="GitHub repository in owner/name form")
    ap.add_argument("--json", action="store_true", help="emit JSON output")
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="show target repository and Ledger head")
    sub.add_parser("doctor", help="validate trusted repository prerequisites")

    req = sub.add_parser("request", help="submit a controlled operation request")
    req.add_argument("operation", help="operation name, e.g. experiment.create")
    req.add_argument("--input", help="operation input JSON object")
    req.add_argument("--input-file", help="path to operation input JSON")
    req.add_argument("--preconditions", help="preconditions JSON object")
    req.add_argument("--preconditions-file", help="path to preconditions JSON")
    req.add_argument("--actor-claim", help="descriptive actor claim; not an auth boundary")
    req.add_argument("--request-id", help="stable idempotency key")

    rec = sub.add_parser("reconcile", help="resolve an uncertain request from remote evidence")
    rec.add_argument("request_id")

    init = sub.add_parser("initialize", help="initialize canonical experiment source refs from Ledger")
    init.add_argument("experiment_id", help="canonical experiment id, e.g. EXP-21")

    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)

    try:
        transport = GitHubTransport(args.repo)
        client = GameExpClient(transport)

        if args.command == "status":
            result = client.status()
        elif args.command == "doctor":
            result = client.doctor()
        elif args.command == "request":
            input_value = _load_object(args.input, args.input_file, label="input")
            preconditions = _load_object(
                args.preconditions,
                args.preconditions_file,
                label="preconditions",
            )
            result = client.submit(
                operation=args.operation,
                input_value=input_value,
                preconditions=preconditions,
                actor_claim=args.actor_claim,
                request_id=args.request_id,
            )
        elif args.command == "reconcile":
            result = client.reconcile(args.request_id)
        elif args.command == "initialize":
            result = client.initialize(args.experiment_id)
        else:
            ap.error("unknown command")
            return 2
    except (ProtocolError, ClientError, OSError) as exc:
        result = {
            "status": "REJECTED",
            "error": str(exc),
        }
        _print_result(result, as_json=args.json)
        return 2

    _print_result(result, as_json=args.json)
    status = result.get("status")
    return 0 if status in {"PASS", "ACCEPTED", "COMMITTED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
