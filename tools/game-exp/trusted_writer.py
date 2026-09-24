from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from protocol_core import (
    ProtocolError,
    canonical_json_bytes,
    decode_payload_b64,
    digest_object,
    validate_request_id,
)


class WriterError(RuntimeError):
    pass


def run(args, cwd=None, env=None, check=True):
    proc = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and proc.returncode != 0:
        raise WriterError(
            f"command failed ({proc.returncode}): {' '.join(args)}\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return proc


def read_record(repo_dir: Path, ref: str, target: str):
    proc = run(["git", "show", f"{ref}:{target}"], cwd=repo_dir, check=False)
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise WriterError(f"invalid Ledger record at {ref}:{target}") from exc


def emit(status: str, **fields):
    out = {"status": status, **fields}
    print(json.dumps(out, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--request-id", required=True)
    ap.add_argument("--expected-head", required=True)
    ap.add_argument("--payload-b64", required=True)
    ap.add_argument("--ssh-key", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--run-attempt", required=True)
    ap.add_argument("--workflow-source-sha", required=True)
    args = ap.parse_args()

    validate_request_id(args.request_id)
    if not re.fullmatch(r"[0-9a-f]{40}", args.expected_head):
        raise ProtocolError("expected_head must be a 40-character commit SHA")

    payload = decode_payload_b64(args.payload_b64)
    payload_digest = digest_object(payload)
    target = f"operations/{args.request_id}.json"

    env = os.environ.copy()
    env["GIT_SSH_COMMAND"] = (
        f'ssh -i "{args.ssh_key}" -o IdentitiesOnly=yes '
        "-o StrictHostKeyChecking=accept-new"
    )

    with tempfile.TemporaryDirectory(prefix="game-exp-ledger-") as td:
        repo_dir = Path(td) / "ledger"
        run(
            [
                "git",
                "clone",
                "--single-branch",
                "--branch",
                "game-exp/ledger",
                f"git@github.com:{args.repo}.git",
                str(repo_dir),
            ],
            env=env,
        )

        current = run(["git", "rev-parse", "HEAD"], cwd=repo_dir).stdout.strip()
        existing = read_record(repo_dir, "HEAD", target)
        if existing is not None:
            if existing.get("payload_digest") == payload_digest:
                emit(
                    "COMMITTED",
                    request_id=args.request_id,
                    payload_digest=payload_digest,
                    ledger_head=current,
                    record_path=target,
                    replayed=True,
                )
                return 0
            emit(
                "REQUEST_ID_CONFLICT",
                request_id=args.request_id,
                payload_digest=payload_digest,
                ledger_head=current,
                record_path=target,
                replayed=False,
            )
            return 43

        if current != args.expected_head:
            emit(
                "HEAD_CONFLICT",
                request_id=args.request_id,
                payload_digest=payload_digest,
                ledger_head=current,
                expected_head=args.expected_head,
                record_path=target,
                replayed=False,
            )
            return 42

        record = {
            "expected_head": args.expected_head,
            "github_run_attempt": str(args.run_attempt),
            "github_run_id": str(args.run_id),
            "issuer": "game-exp-trusted-writer",
            "payload": payload,
            "payload_digest": payload_digest,
            "request_id": args.request_id,
            "workflow_source_sha": args.workflow_source_sha,
        }
        path = repo_dir / target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical_json_bytes(record) + b"\n")

        run(["git", "config", "user.name", "game-exp-trusted-writer"], cwd=repo_dir)
        run(
            [
                "git",
                "config",
                "user.email",
                "game-exp-trusted-writer@users.noreply.github.com",
            ],
            cwd=repo_dir,
        )
        run(["git", "add", target], cwd=repo_dir)
        run(["git", "commit", "-m", f"game-exp request {args.request_id}"], cwd=repo_dir)

        push = run(
            ["git", "push", "origin", "HEAD:game-exp/ledger"],
            cwd=repo_dir,
            env=env,
            check=False,
        )
        if push.returncode == 0:
            head = run(["git", "rev-parse", "HEAD"], cwd=repo_dir).stdout.strip()
            emit(
                "COMMITTED",
                request_id=args.request_id,
                payload_digest=payload_digest,
                ledger_head=head,
                record_path=target,
                replayed=False,
            )
            return 0

        run(["git", "fetch", "origin", "game-exp/ledger"], cwd=repo_dir, env=env)
        remote_head = run(
            ["git", "rev-parse", "origin/game-exp/ledger"], cwd=repo_dir
        ).stdout.strip()
        remote = read_record(repo_dir, "origin/game-exp/ledger", target)
        if remote is not None and remote.get("payload_digest") == payload_digest:
            emit(
                "COMMITTED",
                request_id=args.request_id,
                payload_digest=payload_digest,
                ledger_head=remote_head,
                record_path=target,
                replayed=True,
                recovered_after_push_uncertainty=True,
            )
            return 0

        emit(
            "CONFLICT",
            request_id=args.request_id,
            payload_digest=payload_digest,
            ledger_head=remote_head,
            record_path=target,
            replayed=False,
            push_stderr=push.stderr[-2000:],
        )
        return 44


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ProtocolError, WriterError) as exc:
        print(
            json.dumps(
                {"status": "INVALID_REQUEST", "error": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        raise SystemExit(41)
