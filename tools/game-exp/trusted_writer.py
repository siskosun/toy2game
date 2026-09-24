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

from domain_core import DomainError, TrustedActorContext, TrustedBindingContext, TrustedCandidateContext, TrustedRehearsalContext, TrustedRetentionContext, plan_domain_mutation, validate_manifest
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


def resolve_trusted_candidate(
    payload: dict,
    *,
    authority: str,
    context_path: str | None,
) -> TrustedCandidateContext | None:
    if payload.get("kind") != "operation_request" or payload.get("operation") != "candidate.register":
        return None
    if authority != "candidate":
        raise DomainError(
            "candidate.register requires trusted candidate authority",
            code="DOMAIN_AUTHORIZATION_FAILED",
        )
    if not context_path:
        raise DomainError(
            "trusted Candidate context file is required",
            code="DOMAIN_AUTHORIZATION_FAILED",
        )
    try:
        value = json.loads(Path(context_path).read_text(encoding="utf-8"))
    except Exception as exc:
        raise DomainError(
            f"invalid trusted Candidate context: {exc}",
            code="DOMAIN_AUTHORIZATION_FAILED",
        ) from exc
    if not isinstance(value, dict):
        raise DomainError(
            "trusted Candidate context must be an object",
            code="DOMAIN_AUTHORIZATION_FAILED",
        )
    required = {
        "experiment_id",
        "candidate_id",
        "source_sha",
        "manifest_digest",
        "artifact_digest",
        "policy_digest",
        "workflow_source_sha",
        "run_id",
        "run_attempt",
        "checks",
        "retention",
        "attestation",
    }
    if set(value) != required:
        raise DomainError(
            "trusted Candidate context keys mismatch",
            code="DOMAIN_AUTHORIZATION_FAILED",
        )
    if not isinstance(value["checks"], list):
        raise DomainError(
            "trusted Candidate checks must be a list",
            code="DOMAIN_AUTHORIZATION_FAILED",
        )
    if not isinstance(value["retention"], dict) or not isinstance(value["attestation"], dict):
        raise DomainError(
            "trusted Candidate retention/attestation must be objects",
            code="DOMAIN_AUTHORIZATION_FAILED",
        )
    return TrustedCandidateContext(
        experiment_id=str(value["experiment_id"]),
        candidate_id=str(value["candidate_id"]),
        source_sha=str(value["source_sha"]),
        manifest_digest=str(value["manifest_digest"]),
        artifact_digest=str(value["artifact_digest"]),
        policy_digest=str(value["policy_digest"]),
        workflow_source_sha=str(value["workflow_source_sha"]),
        run_id=str(value["run_id"]),
        run_attempt=str(value["run_attempt"]),
        checks=tuple(value["checks"]),
        retention=dict(value["retention"]),
        attestation=dict(value["attestation"]),
    )


def resolve_trusted_rehearsal(
    repo: str,
    payload: dict,
    *,
    authority: str,
    context_path: str | None,
) -> TrustedRehearsalContext | None:
    if payload.get("kind") != "operation_request" or payload.get("operation") != "rehearsal.register":
        return None
    if authority != "rehearsal":
        raise DomainError(
            "rehearsal.register requires trusted rehearsal authority",
            code="DOMAIN_AUTHORIZATION_FAILED",
        )
    if not context_path:
        raise DomainError(
            "trusted Rehearsal context file is required",
            code="DOMAIN_AUTHORIZATION_FAILED",
        )
    try:
        value = json.loads(Path(context_path).read_text(encoding="utf-8"))
    except Exception as exc:
        raise DomainError(
            f"invalid trusted Rehearsal context: {exc}",
            code="DOMAIN_AUTHORIZATION_FAILED",
        ) from exc
    if not isinstance(value, dict):
        raise DomainError(
            "trusted Rehearsal context must be an object",
            code="DOMAIN_AUTHORIZATION_FAILED",
        )
    required = {
        "experiment_id",
        "candidate_id",
        "rehearsal_id",
        "main_sha",
        "source_sha",
        "integration_sha",
        "integration_tree_sha",
        "rehearsal_ref",
        "workflow_source_sha",
        "run_id",
        "run_attempt",
        "policy_digest",
        "scope_digest",
        "checks",
    }
    if set(value) != required or not isinstance(value["checks"], list):
        raise DomainError(
            "trusted Rehearsal context keys mismatch",
            code="DOMAIN_AUTHORIZATION_FAILED",
        )

    experiment_id = str(value["experiment_id"])
    candidate_id = str(value["candidate_id"])
    rehearsal_id = str(value["rehearsal_id"])
    main_sha = str(value["main_sha"])
    source_sha = str(value["source_sha"])
    integration_sha = str(value["integration_sha"])
    integration_tree_sha = str(value["integration_tree_sha"])
    rehearsal_ref = str(value["rehearsal_ref"])
    workflow_source_sha = str(value["workflow_source_sha"])
    run_id = str(value["run_id"])
    run_attempt = str(value["run_attempt"])
    policy_digest = str(value["policy_digest"])
    scope_digest = str(value["scope_digest"])

    match = re.fullmatch(r"EXP-([1-9][0-9]*)", experiment_id)
    if not match:
        raise DomainError("trusted Rehearsal experiment_id is invalid")
    issue = match.group(1)
    expected_ref = f"refs/tags/exp-rehearsal/{issue}/{rehearsal_id}"
    if rehearsal_ref != expected_ref:
        raise DomainError(
            "trusted Rehearsal ref is not canonical",
            code="DOMAIN_REHEARSAL_CONFLICT",
        )

    ref_suffix = "/git/ref/tags/" + "/".join(
        urllib.parse.quote(part, safe="")
        for part in f"exp-rehearsal/{issue}/{rehearsal_id}".split("/")
    )
    ref_data = github_json(repo, ref_suffix)
    ref_object = ref_data.get("object") or {}
    if ref_object.get("type") != "tag" or not isinstance(ref_object.get("sha"), str):
        raise DomainError(
            "Rehearsal anchor must be an annotated tag",
            code="DOMAIN_REHEARSAL_CONFLICT",
        )
    tag_data = github_json(
        repo,
        f"/git/tags/{urllib.parse.quote(ref_object['sha'], safe='')}",
    )
    tag_object = tag_data.get("object") or {}
    if tag_object.get("type") != "commit" or tag_object.get("sha") != integration_sha:
        raise DomainError(
            "Rehearsal anchor target differs from integration_sha",
            code="DOMAIN_REHEARSAL_CONFLICT",
        )
    message = tag_data.get("message")
    if not isinstance(message, str):
        raise DomainError("Rehearsal anchor message missing")
    metadata: dict[str, str] = {}
    for line in message.splitlines():
        if ": " in line:
            key, item = line.split(": ", 1)
            metadata[key.strip()] = item.strip()
    expected_metadata = {
        "game-exp-experiment": experiment_id,
        "game-exp-candidate-id": candidate_id,
        "game-exp-rehearsal-id": rehearsal_id,
        "game-exp-main-sha": main_sha,
        "game-exp-source-sha": source_sha,
        "game-exp-integration-sha": integration_sha,
        "game-exp-integration-tree-sha": integration_tree_sha,
        "game-exp-workflow-source-sha": workflow_source_sha,
        "game-exp-run-id": run_id,
        "game-exp-run-attempt": run_attempt,
        "game-exp-policy-digest": policy_digest,
        "game-exp-scope-digest": scope_digest,
    }
    for key, expected in expected_metadata.items():
        if metadata.get(key) != expected:
            raise DomainError(
                f"Rehearsal anchor metadata mismatch for {key}",
                code="DOMAIN_REHEARSAL_CONFLICT",
            )

    commit_data = github_json(
        repo,
        f"/git/commits/{urllib.parse.quote(integration_sha, safe='')}",
    )
    tree = commit_data.get("tree") or {}
    parents = commit_data.get("parents")
    if tree.get("sha") != integration_tree_sha or not isinstance(parents, list):
        raise DomainError(
            "Rehearsal integration commit tree/parents are invalid",
            code="DOMAIN_REHEARSAL_CONFLICT",
        )
    parent_shas = [parent.get("sha") for parent in parents if isinstance(parent, dict)]
    if parent_shas != [main_sha, source_sha]:
        raise DomainError(
            "Rehearsal integration parents differ from main/Candidate source",
            code="DOMAIN_REHEARSAL_CONFLICT",
        )

    main_ref = github_json(repo, "/git/ref/heads/main")
    current_main = (main_ref.get("object") or {}).get("sha")
    if current_main != main_sha:
        raise DomainError(
            "Rehearsal is stale because main advanced before finalization",
            code="DOMAIN_REHEARSAL_CONFLICT",
        )

    run_data = github_json(
        repo,
        f"/actions/runs/{urllib.parse.quote(run_id, safe='')}",
    )
    if (
        str(run_data.get("run_attempt")) != run_attempt
        or run_data.get("event") != "workflow_dispatch"
        or run_data.get("path") != ".github/workflows/game-exp-rehearsal.yml"
        or run_data.get("head_sha") != workflow_source_sha
        or run_data.get("status") != "in_progress"
    ):
        raise DomainError(
            "Rehearsal context is not from the active trusted rehearsal workflow",
            code="DOMAIN_REHEARSAL_CONFLICT",
        )

    return TrustedRehearsalContext(
        experiment_id=experiment_id,
        candidate_id=candidate_id,
        rehearsal_id=rehearsal_id,
        main_sha=main_sha,
        source_sha=source_sha,
        integration_sha=integration_sha,
        integration_tree_sha=integration_tree_sha,
        rehearsal_ref=rehearsal_ref,
        workflow_source_sha=workflow_source_sha,
        run_id=run_id,
        run_attempt=run_attempt,
        policy_digest=policy_digest,
        scope_digest=scope_digest,
        checks=tuple(value["checks"]),
    )


def resolve_trusted_retention(
    repo: str,
    payload: dict,
    repo_dir: Path,
) -> TrustedRetentionContext | None:
    if payload.get("kind") != "operation_request" or payload.get("operation") != "experiment.decision":
        return None
    input_value = payload.get("input")
    if not isinstance(input_value, dict) or input_value.get("to_state") != "PROMISING":
        return None
    experiment_id = input_value.get("experiment_id")
    if not isinstance(experiment_id, str) or not experiment_id:
        raise DomainError("PROMISING decision experiment_id is invalid")

    try:
        state = json.loads(
            (repo_dir / f"experiments/{experiment_id}/state.json").read_text(encoding="utf-8")
        )
        candidate_id = state["current_candidate_id"]
        candidate = json.loads(
            (
                repo_dir
                / f"experiments/{experiment_id}/candidates/{candidate_id}.json"
            ).read_text(encoding="utf-8")
        )
        retention = candidate["retention"]
        release_tag = retention["release_tag"]
    except Exception as exc:
        raise DomainError(
            f"cannot resolve current Candidate retention: {exc}",
            code="DOMAIN_PREREQUISITE_MISSING",
        ) from exc

    try:
        release = github_json(
            repo,
            f"/releases/tags/{urllib.parse.quote(str(release_tag), safe='')}",
        )
    except WriterError as exc:
        raise DomainError(
            f"current Level-3 release could not be verified: {exc}",
            code="DOMAIN_PREREQUISITE_MISSING",
        ) from exc

    assets = release.get("assets")
    if not isinstance(assets, list):
        raise DomainError(
            "current Level-3 release has no asset list",
            code="DOMAIN_PREREQUISITE_MISSING",
        )
    matches = [
        asset
        for asset in assets
        if isinstance(asset, dict)
        and asset.get("name") == "candidate.tgz"
        and asset.get("state") == "uploaded"
    ]
    if len(matches) != 1:
        raise DomainError(
            "current Level-3 release must contain exactly one uploaded candidate.tgz",
            code="DOMAIN_PREREQUISITE_MISSING",
        )
    asset = matches[0]
    release_id = release.get("id")
    asset_id = asset.get("id")
    if not isinstance(release_id, int) or not isinstance(asset_id, int):
        raise DomainError(
            "current Level-3 release identity is incomplete",
            code="DOMAIN_PREREQUISITE_MISSING",
        )
    if (
        release.get("tag_name") != release_tag
        or release.get("immutable") is not True
        or release.get("draft") is not False
        or release.get("prerelease") is not False
    ):
        raise DomainError(
            "current Level-3 release is missing immutable published guarantees",
            code="DOMAIN_PREREQUISITE_MISSING",
        )
    artifact_digest = asset.get("digest")
    if not isinstance(artifact_digest, str):
        raise DomainError(
            "current Level-3 asset digest is unavailable",
            code="DOMAIN_PREREQUISITE_MISSING",
        )

    return TrustedRetentionContext(
        experiment_id=experiment_id,
        candidate_id=str(candidate_id),
        release_id=str(release_id),
        release_tag=str(release_tag),
        release_url=str(release.get("html_url") or ""),
        immutable=True,
        target_commitish=str(release.get("target_commitish") or ""),
        artifact_name="candidate.tgz",
        artifact_digest=artifact_digest,
        asset_id=str(asset_id),
    )


def resolve_trusted_actor(repo: str, payload: dict) -> TrustedActorContext | None:
    if payload.get("kind") != "operation_request":
        return None
    if payload.get("operation") not in {"experiment.decision", "review.record"}:
        return None

    actor_login = os.environ.get("GAME_EXP_ACTOR_LOGIN")
    if not actor_login:
        raise DomainError(
            "trusted GitHub actor login is unavailable for human-gated operation",
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
    ap.add_argument("--authority", choices=("request", "candidate", "rehearsal"), default="request")
    ap.add_argument("--trusted-context-json")
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
        trusted_candidate = resolve_trusted_candidate(
            payload,
            authority=args.authority,
            context_path=args.trusted_context_json,
        )
        trusted_rehearsal = resolve_trusted_rehearsal(
            args.repo,
            payload,
            authority=args.authority,
            context_path=args.trusted_context_json,
        )
        trusted_retention = resolve_trusted_retention(
            args.repo,
            payload,
            repo_dir,
        )
        domain_plan = plan_domain_mutation(
            repo_dir=repo_dir,
            payload=payload,
            request_id=args.request_id,
            payload_digest=payload_digest,
            repository_full_name=args.repo,
            trusted_binding=trusted_binding,
            trusted_actor=trusted_actor,
            trusted_candidate=trusted_candidate,
            trusted_retention=trusted_retention,
            trusted_rehearsal=trusted_rehearsal,
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
