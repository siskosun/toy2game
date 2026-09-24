from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from domain_core import DomainError, TrustedActorContext, TrustedBindingContext, plan_domain_mutation, validate_manifest
from protocol_core import (
    ProtocolError,
    canonical_json_bytes,
    decode_payload_b64,
    digest_object,
    validate_request_id,
)


class WriterError(RuntimeError):
    pass


def github_json(repo: str, suffix: str) -> dict:
    token = os.environ.get("GAME_EXP_GITHUB_TOKEN")
    if not token:
        raise WriterError("GAME_EXP_GITHUB_TOKEN is required for trusted domain resolution")
    url = "https://api.github.com/repos/" + repo + suffix
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "game-exp-trusted-writer",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            value = json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[-1000:]
        raise WriterError(f"GitHub trusted resolver failed ({exc.code}) for {suffix}: {body}") from exc
    except Exception as exc:
        raise WriterError(f"GitHub trusted resolver failed for {suffix}: {exc}") from exc
    if not isinstance(value, dict):
        raise WriterError(f"GitHub trusted resolver returned non-object for {suffix}")
    return value


def resolve_trusted_actor(repo: str, payload: dict) -> TrustedActorContext | None:
    if payload.get("kind") != "operation_request" or payload.get("operation") != "experiment.decision":
        return None

    actor_login = os.environ.get("GAME_EXP_ACTOR_LOGIN")
    if not actor_login:
        raise DomainError(
            "trusted GitHub actor login is unavailable",
            code="DOMAIN_AUTHORIZATION_FAILED",
        )
    actor_meta = github_json(
        repo,
        f"/collaborators/{urllib.parse.quote(actor_login, safe='')}/permission",
    )
    permission = actor_meta.get("permission")
    user = actor_meta.get("user")
    if not isinstance(permission, str) or not isinstance(user, dict):
        raise DomainError(
            "trusted collaborator permission response is incomplete",
            code="DOMAIN_AUTHORIZATION_FAILED",
        )
    login = user.get("login")
    user_id = user.get("id")
    if login != actor_login or not isinstance(user_id, int):
        raise DomainError(
            "trusted collaborator identity mismatch",
            code="DOMAIN_AUTHORIZATION_FAILED",
        )
    return TrustedActorContext(
        login=login,
        user_id=str(user_id),
        permission=permission,
    )


def resolve_trusted_binding(repo: str, payload: dict) -> TrustedBindingContext | None:
    if payload.get("kind") != "operation_request" or payload.get("operation") != "experiment.bind":
        return None

    input_value = payload.get("input")
    if not isinstance(input_value, dict) or set(input_value) != {"manifest"}:
        raise DomainError("experiment.bind input must contain exactly manifest")
    manifest = validate_manifest(input_value.get("manifest"))
    exp = manifest["experiment"]
    issue_number = exp["issue_number"]
    parent_sha = manifest["parent"]["commit"]

    repo_meta = github_json(repo, "")
    issue_meta = github_json(repo, f"/issues/{urllib.parse.quote(issue_number, safe='')}")
    if "pull_request" in issue_meta:
        raise DomainError("experiment binding requires a GitHub Issue, not a pull request")
    commit_meta = github_json(repo, f"/commits/{urllib.parse.quote(parent_sha, safe='')}")
    resolved_sha = commit_meta.get("sha")
    if resolved_sha != parent_sha:
        raise DomainError(
            f"trusted commit resolver returned {resolved_sha!r}, expected {parent_sha!r}",
            code="DOMAIN_PARENT_CONFLICT",
        )

    return TrustedBindingContext(
        host="github.com",
        repository_id=str(repo_meta.get("id")),
        issue_id=str(issue_meta.get("id")),
        issue_number=str(issue_meta.get("number")),
        parent_sha=resolved_sha,
    )


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

        trusted_binding = resolve_trusted_binding(args.repo, payload)
        trusted_actor = resolve_trusted_actor(args.repo, payload)
        domain_plan = plan_domain_mutation(
            repo_dir=repo_dir,
            payload=payload,
            request_id=args.request_id,
            payload_digest=payload_digest,
            repository_full_name=args.repo,
            trusted_binding=trusted_binding,
            trusted_actor=trusted_actor,
        )
        post_domain_digest = digest_object(payload)
        if post_domain_digest != payload_digest:
            raise WriterError(
                "trusted domain planning mutated the request payload; "
                f"before={payload_digest} after={post_domain_digest}"
            )

        record = {
            "expected_head": args.expected_head,
            "github_run_attempt": str(args.run_attempt),
            "github_run_id": str(args.run_id),
            "issuer": "game-exp-trusted-writer",
            "payload": payload,
            "payload_digest": payload_digest,
            "request_id": args.request_id,
            "workflow_source_sha": args.workflow_source_sha,
            "domain_status": domain_plan.status,
            "domain_experiment_id": domain_plan.experiment_id,
            "domain_paths": domain_plan.paths,
        }
        path = repo_dir / target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical_json_bytes(record) + b"\n")
        for domain_path, domain_value in domain_plan.writes.items():
            target_path = repo_dir / domain_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(canonical_json_bytes(domain_value) + b"\n")

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
        run(["git", "add", target, *domain_plan.paths], cwd=repo_dir)
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
                domain_status=domain_plan.status,
                domain_experiment_id=domain_plan.experiment_id,
                domain_paths=domain_plan.paths,
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
                domain_status=remote.get("domain_status"),
                domain_experiment_id=remote.get("domain_experiment_id"),
                domain_paths=remote.get("domain_paths", []),
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
    except DomainError as exc:
        print(
            json.dumps(
                {"status": exc.code, "error": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        raise SystemExit(45)
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
