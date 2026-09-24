from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from domain_core import (
    DomainError,
    TrustedBindingContext,
    TrustedCandidateContext,
    plan_domain_mutation,
    validate_manifest,
)
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


def resolve_trusted_candidate(repo: str, payload: dict) -> TrustedCandidateContext | None:
    if payload.get("kind") != "operation_request" or payload.get("operation") != "candidate.attest":
        return None
    input_value = payload.get("input")
    if not isinstance(input_value, dict):
        raise DomainError("candidate.attest input must be an object")

    names = (
        "experiment_id",
        "candidate_id",
        "source_sha",
        "source_anchor_ref",
        "artifact_digest",
        "manifest_digest",
        "workflow_source_sha",
        "run_id",
        "run_attempt",
        "policy_digest",
        "dependency_lock_digest",
        "environment_digest",
    )
    fields = {name: input_value.get(name) for name in names}
    if not all(isinstance(value, str) and value for value in fields.values()):
        raise DomainError("candidate.attest trusted-resolver fields are incomplete")

    retention = input_value.get("retention")
    if not isinstance(retention, dict) or not isinstance(retention.get("release_tag"), str):
        raise DomainError("candidate.attest retention release_tag missing")
    release_tag = retention["release_tag"]

    experiment_id = fields["experiment_id"]
    candidate_id = fields["candidate_id"]
    source_sha = fields["source_sha"]
    source_anchor_ref = fields["source_anchor_ref"]
    artifact_digest = fields["artifact_digest"]
    manifest_digest = fields["manifest_digest"]
    workflow_source_sha = fields["workflow_source_sha"]
    run_id = fields["run_id"]
    run_attempt = fields["run_attempt"]
    policy_digest = fields["policy_digest"]
    dependency_lock_digest = fields["dependency_lock_digest"]
    environment_digest = fields["environment_digest"]

    match = re.fullmatch(r"EXP-([1-9][0-9]*)", experiment_id)
    if not match:
        raise DomainError("candidate experiment_id is invalid")
    issue = match.group(1)
    expected_ref = f"refs/tags/exp-candidate/{issue}/{candidate_id}"
    if source_anchor_ref != expected_ref:
        raise DomainError("candidate source anchor ref is not canonical")

    ref_suffix = "/git/ref/tags/" + "/".join(
        urllib.parse.quote(part, safe="")
        for part in f"exp-candidate/{issue}/{candidate_id}".split("/")
    )
    ref_data = github_json(repo, ref_suffix)
    ref_object = ref_data.get("object") or {}
    if ref_object.get("type") != "tag" or not isinstance(ref_object.get("sha"), str):
        raise DomainError("candidate source anchor must be an annotated tag")
    tag_data = github_json(
        repo,
        f"/git/tags/{urllib.parse.quote(ref_object['sha'], safe='')}",
    )
    tag_object = tag_data.get("object") or {}
    if tag_object.get("type") != "commit" or tag_object.get("sha") != source_sha:
        raise DomainError(
            "candidate source anchor target differs from candidate source_sha",
            code="DOMAIN_CANDIDATE_CONFLICT",
        )
    message = tag_data.get("message")
    if not isinstance(message, str):
        raise DomainError("candidate source anchor message missing")
    metadata = {}
    for line in message.splitlines():
        if ": " in line:
            key, value = line.split(": ", 1)
            metadata[key.strip()] = value.strip()
    expected_metadata = {
        "game-exp-experiment": experiment_id,
        "game-exp-candidate-id": candidate_id,
        "game-exp-source-sha": source_sha,
        "game-exp-artifact-digest": artifact_digest,
        "game-exp-manifest-digest": manifest_digest,
        "game-exp-workflow-source-sha": workflow_source_sha,
        "game-exp-run-id": run_id,
        "game-exp-run-attempt": run_attempt,
        "game-exp-policy-digest": policy_digest,
        "game-exp-dependency-lock-digest": dependency_lock_digest,
        "game-exp-environment-digest": environment_digest,
        "game-exp-release-tag": release_tag,
    }
    for key, value in expected_metadata.items():
        if metadata.get(key) != value:
            raise DomainError(
                f"candidate source anchor metadata mismatch for {key}",
                code="DOMAIN_CANDIDATE_CONFLICT",
            )

    release = github_json(
        repo,
        f"/releases/tags/{urllib.parse.quote(release_tag, safe='')}",
    )
    if release.get("immutable") is not True:
        raise DomainError("candidate retention release is not immutable")
    if release.get("target_commitish") != source_sha:
        raise DomainError(
            "candidate retention release target differs from source_sha",
            code="DOMAIN_CANDIDATE_CONFLICT",
        )
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise DomainError("candidate retention release assets missing")
    artifact_assets = [
        asset
        for asset in assets
        if isinstance(asset, dict) and asset.get("name") == "candidate.tgz"
    ]
    if len(artifact_assets) != 1:
        raise DomainError("candidate retention must contain exactly one candidate.tgz")
    artifact_asset = artifact_assets[0]
    if (
        artifact_asset.get("digest") != artifact_digest
        or not isinstance(artifact_asset.get("size"), int)
        or artifact_asset["size"] <= 0
    ):
        raise DomainError(
            "candidate retention asset digest/size mismatch",
            code="DOMAIN_CANDIDATE_CONFLICT",
        )

    lock_data = github_json(
        repo,
        f"/contents/package-lock.json?ref={urllib.parse.quote(source_sha, safe='')}",
    )
    encoded_lock = lock_data.get("content")
    if lock_data.get("encoding") != "base64" or not isinstance(encoded_lock, str):
        raise DomainError("candidate dependency lock cannot be read from source")
    try:
        lock_bytes = base64.b64decode(encoded_lock, validate=False)
    except Exception as exc:
        raise DomainError("candidate dependency lock base64 is invalid") from exc
    actual_lock_digest = "sha256:" + hashlib.sha256(lock_bytes).hexdigest()
    if actual_lock_digest != dependency_lock_digest:
        raise DomainError(
            "candidate dependency lock digest mismatch",
            code="DOMAIN_CANDIDATE_CONFLICT",
        )

    run_data = github_json(
        repo,
        f"/actions/runs/{urllib.parse.quote(run_id, safe='')}",
    )
    if str(run_data.get("run_attempt")) != run_attempt:
        raise DomainError(
            "candidate workflow run_attempt mismatch",
            code="DOMAIN_CANDIDATE_CONFLICT",
        )
    if run_data.get("event") != "workflow_dispatch":
        raise DomainError("candidate workflow must be workflow_dispatch")
    if run_data.get("path") != ".github/workflows/game-exp-candidate.yml":
        raise DomainError("candidate evidence is not from the trusted candidate workflow")
    if run_data.get("head_sha") != workflow_source_sha:
        raise DomainError(
            "candidate workflow source SHA mismatch",
            code="DOMAIN_CANDIDATE_CONFLICT",
        )
    if run_data.get("status") != "in_progress":
        raise DomainError(
            "candidate workflow must finalize while its trusted run is in progress",
            code="DOMAIN_CANDIDATE_CONFLICT",
        )

    return TrustedCandidateContext(
        experiment_id=experiment_id,
        candidate_id=candidate_id,
        source_anchor_ref=source_anchor_ref,
        source_sha=source_sha,
        artifact_digest=artifact_digest,
        manifest_digest=manifest_digest,
        workflow_source_sha=workflow_source_sha,
        run_id=run_id,
        run_attempt=run_attempt,
        policy_digest=policy_digest,
        dependency_lock_digest=dependency_lock_digest,
        environment_digest=environment_digest,
        release_tag=release_tag,
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
        trusted_candidate = resolve_trusted_candidate(args.repo, payload)
        domain_plan = plan_domain_mutation(
            repo_dir=repo_dir,
            payload=payload,
            request_id=args.request_id,
            payload_digest=payload_digest,
            repository_full_name=args.repo,
            trusted_binding=trusted_binding,
            trusted_candidate=trusted_candidate,
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
