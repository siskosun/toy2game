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
    if result.get("operation_status"):
        print(f"operation_status: {result['operation_status']}")
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

    if "experiments" in result:
        if result.get("snapshot_head"):
            print(f"snapshot_head: {result['snapshot_head']}")
        print(f"experiments: {result.get('count', len(result['experiments']))}")
        for row in result["experiments"]:
            title = row.get("title") or ""
            print(
                f"{row.get('experiment_id', '?'):8} "
                f"{row.get('lifecycle', 'UNKNOWN'):10} "
                f"{row.get('next_gate', 'UNKNOWN'):36} "
                f"{title}"
            )


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="game-exp",
        description="Harness-neutral client for the trusted game-exp contract.",
    )
    ap.add_argument("--repo", help="GitHub repository in owner/name form")
    ap.add_argument("--json", action="store_true", help="emit JSON output")
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="show repository and authoritative Ledger head")
    sub.add_parser("access-check", help="show current repository access snapshot")
    sub.add_parser("capabilities", help="show public contract/features and recovery support")

    board = sub.add_parser("board", help="show one consistent experiment Board snapshot")
    board.add_argument("--query")
    board.add_argument("--subject-id")
    board.add_argument("--lifecycle")
    board.add_argument("--attention-only", action="store_true")

    experiment = sub.add_parser("experiment", help="show one authoritative experiment")
    experiment.add_argument("experiment_id")

    subject = sub.add_parser("subject", help="show one subject/prototype panel")
    subject.add_argument("subject_id")

    handoff = sub.add_parser("handoff", help="build Godot Prototype Studio handoff package")
    handoff.add_argument("experiment_id")

    notifications = sub.add_parser("notifications", help="read resumable collaboration events")
    notifications.add_argument("--viewer")
    notifications.add_argument("--subject-id")
    notifications.add_argument("--limit", type=int, default=50)
    notifications.add_argument("--after", help="checkpoint cursor from a completed earlier feed")
    notifications.add_argument("--cursor", help="page cursor within one pinned feed snapshot")

    doctor = sub.add_parser("doctor", help="validate trusted repository prerequisites")
    doctor.add_argument("--experiment-id")

    req = sub.add_parser("request", help="submit one low-level controlled operation request")
    req.add_argument("operation")
    req.add_argument("--input")
    req.add_argument("--input-file")
    req.add_argument("--preconditions")
    req.add_argument("--preconditions-file")
    req.add_argument("--actor-claim")
    req.add_argument("--request-id", required=True, help="stable cross-interface idempotency key")

    get_op = sub.add_parser("get-operation", help="resolve one operation without resubmitting it")
    get_op.add_argument("request_id")
    reconcile = sub.add_parser("reconcile", help="compatibility alias for get-operation")
    reconcile.add_argument("request_id")
    resume = sub.add_parser("resume-operation", help="resume only an already-claimed async operation")
    resume.add_argument("request_id")

    init = sub.add_parser("initialize", help="initialize canonical experiment source refs")
    init.add_argument("experiment_id")
    init.add_argument("--request-id", required=True)
    init.add_argument("--actor-claim")

    candidate = sub.add_parser("candidate", help="build/register a trusted Candidate")
    candidate.add_argument("experiment_id")
    candidate.add_argument("--request-id", required=True)
    candidate.add_argument("--actor-claim")

    review = sub.add_parser("review", help="record an explicit human PASS/FAIL Review")
    review.add_argument("experiment_id")
    review.add_argument("--outcome", choices=("PASS", "FAIL"), required=True)
    review.add_argument("--notes", required=True)
    review.add_argument("--candidate-id")
    review.add_argument("--request-id", required=True)
    review.add_argument("--actor-claim")

    decision = sub.add_parser("decision", help="submit an explicit human lifecycle decision")
    decision.add_argument("experiment_id")
    decision.add_argument("--to-state", required=True)
    decision.add_argument("--reason", required=True)
    decision.add_argument("--previous-decision-id")
    decision.add_argument("--request-id", required=True)
    decision.add_argument("--actor-claim")

    rehearse = sub.add_parser("rehearse", help="run trusted latest-main integration rehearsal")
    rehearse.add_argument("experiment_id")
    rehearse.add_argument("--request-id", required=True)
    rehearse.add_argument("--actor-claim")

    integrate = sub.add_parser("integrate", help="create/reuse the trusted Integration PR")
    integrate.add_argument("experiment_id")
    integrate.add_argument("--request-id", required=True)
    integrate.add_argument("--actor-claim")

    integrate_finalize = sub.add_parser(
        "integrate-finalize",
        help="verify a merged Integration PR and register INTEGRATED",
    )
    integrate_finalize.add_argument("experiment_id")
    integrate_finalize.add_argument("--pr-number", required=True)
    integrate_finalize.add_argument("--request-id", required=True)
    integrate_finalize.add_argument("--actor-claim")

    archive = sub.add_parser("archive", help="run trusted recoverable Archive")
    archive.add_argument("experiment_id")
    archive.add_argument(
        "--mode",
        choices=("ATOMIC_DELETE", "RETAIN_BRANCH"),
        default="ATOMIC_DELETE",
    )
    archive.add_argument("--request-id", required=True)
    archive.add_argument("--actor-claim")

    archive_abort = sub.add_parser(
        "archive-abort",
        help="abort Archive only while PREPARED and not yet claimed",
    )
    archive_abort.add_argument("experiment_id")
    archive_abort.add_argument("--archive-id", required=True)
    archive_abort.add_argument("--reason", required=True)
    archive_abort.add_argument("--request-id", required=True)
    archive_abort.add_argument("--actor-claim")

    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)

    try:
        transport = GitHubTransport(args.repo)
        client = GameExpClient(transport)

        if args.command == "status":
            result = client.status()
        elif args.command == "access-check":
            result = client.access_check()
        elif args.command == "capabilities":
            result = client.capabilities()
            result["interface"] = {
                "type": "cli",
                "transport": "local-process",
                "write_identity": "local-gh-principal",
            }
        elif args.command == "board":
            result = client.board(
                query=args.query,
                subject_id=args.subject_id,
                lifecycle=args.lifecycle,
                attention_only=args.attention_only,
            )
        elif args.command == "experiment":
            result = client.experiment_get(args.experiment_id)
        elif args.command == "subject":
            result = client.subject_panel(args.subject_id)
        elif args.command == "handoff":
            result = client.prototype_handoff(args.experiment_id)
        elif args.command == "notifications":
            result = client.notification_feed(
                viewer_login=args.viewer,
                subject_id=args.subject_id,
                limit=args.limit,
                after=args.after,
                cursor=args.cursor,
            )
        elif args.command == "doctor":
            result = client.doctor(args.experiment_id)
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
        elif args.command in {"get-operation", "reconcile"}:
            result = client.operation_get(args.request_id)
        elif args.command == "resume-operation":
            result = client.resume_execution(args.request_id)
        elif args.command == "initialize":
            result = client.initialize(
                args.experiment_id,
                request_id=args.request_id,
                actor_claim=args.actor_claim,
            )
        elif args.command == "candidate":
            result = client.candidate(
                args.experiment_id,
                request_id=args.request_id,
                actor_claim=args.actor_claim,
            )
        elif args.command == "review":
            candidate_id = args.candidate_id
            if candidate_id is None:
                projection = client.experiment_get(args.experiment_id)
                if projection.get("status") != "PASS":
                    result = projection
                else:
                    candidate_id = projection.get("state", {}).get("current_candidate_id")
                    if not isinstance(candidate_id, str) or not candidate_id:
                        result = {
                            "status": "REJECTED",
                            "repo": client.transport.repo,
                            "experiment_id": args.experiment_id,
                            "request_id": args.request_id,
                            "error": "experiment has no current Candidate",
                        }
                    else:
                        result = client.submit(
                            operation="review.record",
                            input_value={
                                "experiment_id": args.experiment_id,
                                "candidate_id": candidate_id,
                                "outcome": args.outcome,
                                "notes": args.notes,
                            },
                            actor_claim=args.actor_claim,
                            request_id=args.request_id,
                        )
            else:
                result = client.submit(
                    operation="review.record",
                    input_value={
                        "experiment_id": args.experiment_id,
                        "candidate_id": candidate_id,
                        "outcome": args.outcome,
                        "notes": args.notes,
                    },
                    actor_claim=args.actor_claim,
                    request_id=args.request_id,
                )
        elif args.command == "decision":
            previous_decision_id = args.previous_decision_id
            if previous_decision_id is None:
                projection = client.experiment_get(args.experiment_id)
                if projection.get("status") != "PASS":
                    result = projection
                else:
                    previous_decision_id = projection.get("state", {}).get("last_decision_id")
                    result = client.submit(
                        operation="experiment.decision",
                        input_value={
                            "experiment_id": args.experiment_id,
                            "to_state": args.to_state,
                            "previous_decision_id": previous_decision_id,
                            "reason": args.reason,
                        },
                        actor_claim=args.actor_claim,
                        request_id=args.request_id,
                    )
            else:
                result = client.submit(
                    operation="experiment.decision",
                    input_value={
                        "experiment_id": args.experiment_id,
                        "to_state": args.to_state,
                        "previous_decision_id": previous_decision_id,
                        "reason": args.reason,
                    },
                    actor_claim=args.actor_claim,
                    request_id=args.request_id,
                )
        elif args.command == "rehearse":
            result = client.rehearse(
                args.experiment_id,
                request_id=args.request_id,
                actor_claim=args.actor_claim,
            )
        elif args.command == "integrate":
            result = client.integrate(
                args.experiment_id,
                request_id=args.request_id,
                actor_claim=args.actor_claim,
            )
        elif args.command == "integrate-finalize":
            result = client.integrate_finalize(
                args.experiment_id,
                args.pr_number,
                request_id=args.request_id,
                actor_claim=args.actor_claim,
            )
        elif args.command == "archive":
            result = client.archive(
                args.experiment_id,
                args.mode,
                request_id=args.request_id,
                actor_claim=args.actor_claim,
            )
        elif args.command == "archive-abort":
            result = client.archive_abort(
                args.experiment_id,
                args.archive_id,
                args.reason,
                actor_claim=args.actor_claim,
                request_id=args.request_id,
            )
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
    return 0 if status in {"PASS", "WARN", "ACCEPTED", "COMMITTED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
