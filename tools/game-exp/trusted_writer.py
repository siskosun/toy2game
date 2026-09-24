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

from domain_core import DomainError, TrustedActorContext, TrustedArchiveContext, TrustedBindingContext, TrustedCandidateContext, TrustedIntegrationContext, TrustedRehearsalContext, TrustedRetentionContext, plan_domain_mutation, validate_manifest
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


def github_json_optional(repo: str, suffix: str) -> dict | None:
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
        if exc.code == 404:
            return None
        body = exc.read().decode("utf-8", errors="replace")[-1000:]
        raise WriterError(
            f"GitHub trusted resolver failed ({exc.code}) for {suffix}: {body}"
        ) from exc
    except Exception as exc:
        raise WriterError(f"GitHub trusted resolver failed for {suffix}: {exc}") from exc
    if not isinstance(value, dict):
        raise WriterError(f"GitHub trusted resolver returned non-object for {suffix}")
    return value


def _ledger_object(repo_dir: Path, path: str, *, where: str) -> dict:
    try:
        value = json.loads((repo_dir / path).read_text(encoding="utf-8"))
    except Exception as exc:
        raise DomainError(
            f"{where}: cannot read authoritative Ledger object {path}: {exc}",
            code="DOMAIN_BOUND_EXPERIMENT_INVALID",
        ) from exc
    if not isinstance(value, dict):
        raise DomainError(
            f"{where}: authoritative Ledger object must be an object",
            code="DOMAIN_BOUND_EXPERIMENT_INVALID",
        )
    return value


def _archive_record_for(
    repo_dir: Path,
    experiment_id: str,
    archive_id: str,
) -> dict:
    return _ledger_object(
        repo_dir,
        f"experiments/{experiment_id}/archives/{archive_id}.json",
        where=experiment_id,
    )


def _archive_remote_refs(
    repo: str,
    *,
    branch_ref: str,
    final_tag_ref: str,
) -> tuple[str | None, str | None, dict[str, str] | None]:
    branch_name = branch_ref.removeprefix("refs/heads/")
    tag_name = final_tag_ref.removeprefix("refs/tags/")
    branch_suffix = "/git/ref/heads/" + "/".join(
        urllib.parse.quote(part, safe="") for part in branch_name.split("/")
    )
    tag_suffix = "/git/ref/tags/" + "/".join(
        urllib.parse.quote(part, safe="") for part in tag_name.split("/")
    )

    branch_data = github_json_optional(repo, branch_suffix)
    branch_sha = None
    if branch_data is not None:
        obj = branch_data.get("object") or {}
        if obj.get("type") != "commit" or not isinstance(obj.get("sha"), str):
            raise DomainError(
                "archive branch ref is not a commit ref",
                code="DOMAIN_ARCHIVE_CONFLICT",
            )
        branch_sha = obj["sha"]

    tag_data = github_json_optional(repo, tag_suffix)
    final_target = None
    final_metadata = None
    if tag_data is not None:
        obj = tag_data.get("object") or {}
        if obj.get("type") != "tag" or not isinstance(obj.get("sha"), str):
            raise DomainError(
                "archive final ref must be an annotated tag",
                code="DOMAIN_ARCHIVE_CONFLICT",
            )
        annotated = github_json(
            repo,
            f"/git/tags/{urllib.parse.quote(obj['sha'], safe='')}",
        )
        target = annotated.get("object") or {}
        if target.get("type") != "commit" or not isinstance(target.get("sha"), str):
            raise DomainError(
                "archive final annotated tag must target a commit",
                code="DOMAIN_ARCHIVE_CONFLICT",
            )
        message = annotated.get("message")
        if not isinstance(message, str):
            raise DomainError(
                "archive final annotated tag message is missing",
                code="DOMAIN_ARCHIVE_CONFLICT",
            )
        metadata: dict[str, str] = {}
        for line in message.splitlines():
            if ": " in line:
                key, value = line.split(": ", 1)
                metadata[key.strip()] = value.strip()
        final_target = target["sha"]
        final_metadata = metadata
    return branch_sha, final_target, final_metadata


def _classify_archive_refs(
    *,
    mode: str,
    expected_branch_sha: str,
    branch_sha: str | None,
    final_tag_target: str | None,
) -> str:
    if mode == "ATOMIC_DELETE":
        if final_tag_target == expected_branch_sha and branch_sha is None:
            return "REF_COMMITTED"
        if final_tag_target is None and branch_sha == expected_branch_sha:
            return "NOT_EXECUTED"
        return "REF_CONFLICT"
    if mode == "RETAIN_BRANCH":
        if (
            final_tag_target == expected_branch_sha
            and branch_sha == expected_branch_sha
        ):
            return "REF_COMMITTED"
        if final_tag_target is None and branch_sha == expected_branch_sha:
            return "NOT_EXECUTED"
        return "REF_CONFLICT"
    raise DomainError("unknown archive mode", code="DOMAIN_ARCHIVE_CONFLICT")


def resolve_trusted_archive(
    repo: str,
    payload: dict,
    repo_dir: Path,
    *,
    authority: str,
) -> TrustedArchiveContext | None:
    if payload.get("kind") != "operation_request":
        return None
    operation = payload.get("operation")
    if operation not in {
        "archive.prepare",
        "archive.claim",
        "archive.abort",
        "archive.observe",
        "archive.commit",
    }:
        return None
    input_value = payload.get("input")
    if not isinstance(input_value, dict):
        raise DomainError("archive input must be an object")
    experiment_id = input_value.get("experiment_id")
    if not isinstance(experiment_id, str) or not re.fullmatch(r"EXP-[1-9][0-9]*", experiment_id):
        raise DomainError("archive experiment_id must be EXP-<number>")

    issue = experiment_id.removeprefix("EXP-")
    binding = _ledger_object(
        repo_dir,
        f"experiments/{experiment_id}/binding.json",
        where=experiment_id,
    )
    initialization = binding.get("initialization")
    if not isinstance(initialization, dict):
        raise DomainError(
            "archive binding initialization is missing",
            code="DOMAIN_BOUND_EXPERIMENT_INVALID",
        )
    branch_ref = initialization.get("branch_ref")
    final_tag_ref = initialization.get("final_tag_ref")
    if (
        branch_ref != f"refs/heads/exp/{issue}"
        or final_tag_ref != f"refs/tags/exp-final/{issue}"
    ):
        raise DomainError(
            "archive canonical refs differ from binding",
            code="DOMAIN_ARCHIVE_CONFLICT",
        )

    if operation == "archive.prepare":
        if authority != "request":
            raise DomainError(
                "archive.prepare must enter through normal human request authority",
                code="DOMAIN_AUTHORIZATION_FAILED",
            )
        mode = input_value.get("mode")
        if mode not in {"ATOMIC_DELETE", "RETAIN_BRANCH"}:
            raise DomainError("archive mode is invalid")
        branch_sha, final_target, _final_metadata = _archive_remote_refs(
            repo,
            branch_ref=branch_ref,
            final_tag_ref=final_tag_ref,
        )
        if branch_sha is None:
            raise DomainError(
                "archive prepare requires the experiment branch to exist",
                code="DOMAIN_ARCHIVE_CONFLICT",
            )
        if final_target is not None:
            raise DomainError(
                "archive prepare requires final tag to be absent",
                code="DOMAIN_ARCHIVE_CONFLICT",
            )
        return TrustedArchiveContext(
            action="PREPARE",
            experiment_id=experiment_id,
            archive_id=None,
            mode=mode,
            branch_ref=branch_ref,
            final_tag_ref=final_tag_ref,
            expected_branch_sha=branch_sha,
        )

    if operation == "archive.abort":
        return None
    if authority != "archive":
        raise DomainError(
            f"{operation} requires trusted archive authority",
            code="DOMAIN_AUTHORIZATION_FAILED",
        )

    archive_id = input_value.get("archive_id")
    if not isinstance(archive_id, str):
        raise DomainError("archive_id is required")
    record = _archive_record_for(repo_dir, experiment_id, archive_id)
    mode = record.get("mode")
    expected_branch_sha = record.get("expected_branch_sha")
    if mode not in {"ATOMIC_DELETE", "RETAIN_BRANCH"}:
        raise DomainError("archive record mode is invalid", code="DOMAIN_ARCHIVE_CONFLICT")
    if not isinstance(expected_branch_sha, str) or not re.fullmatch(
        r"[0-9a-f]{40}",
        expected_branch_sha,
    ):
        raise DomainError(
            "archive expected branch SHA is invalid",
            code="DOMAIN_ARCHIVE_CONFLICT",
        )
    for key, expected in (
        ("branch_ref", branch_ref),
        ("final_tag_ref", final_tag_ref),
    ):
        if record.get(key) != expected:
            raise DomainError(
                f"archive record canonical ref mismatch for {key}",
                code="DOMAIN_ARCHIVE_CONFLICT",
            )

    if operation == "archive.claim":
        return TrustedArchiveContext(
            action="CLAIM",
            experiment_id=experiment_id,
            archive_id=archive_id,
            mode=mode,
            branch_ref=branch_ref,
            final_tag_ref=final_tag_ref,
            expected_branch_sha=expected_branch_sha,
        )

    branch_sha, final_target, final_metadata = _archive_remote_refs(
        repo,
        branch_ref=branch_ref,
        final_tag_ref=final_tag_ref,
    )
    ref_status = _classify_archive_refs(
        mode=mode,
        expected_branch_sha=expected_branch_sha,
        branch_sha=branch_sha,
        final_tag_target=final_target,
    )
    if final_target is not None:
        expected_metadata = {
            "game-exp-experiment": experiment_id,
            "game-exp-archive-id": archive_id,
            "game-exp-mode": mode,
            "game-exp-source-sha": expected_branch_sha,
        }
        if final_metadata is None:
            raise DomainError(
                "archive final tag metadata is missing",
                code="DOMAIN_ARCHIVE_CONFLICT",
            )
        for key, expected in expected_metadata.items():
            if final_metadata.get(key) != expected:
                raise DomainError(
                    f"archive final tag metadata mismatch for {key}",
                    code="DOMAIN_ARCHIVE_CONFLICT",
                )
    action = "OBSERVE" if operation == "archive.observe" else "COMMIT"
    return TrustedArchiveContext(
        action=action,
        experiment_id=experiment_id,
        archive_id=archive_id,
        mode=mode,
        branch_ref=branch_ref,
        final_tag_ref=final_tag_ref,
        expected_branch_sha=expected_branch_sha,
        ref_status=ref_status,
        observed_branch_sha=branch_sha,
        observed_final_tag_target=final_target,
    )


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


def _verify_trusted_rehearsal_value(
    repo: str,
    value: dict,
    *,
    expected_run_status: str,
    expected_run_conclusion: str | None,
) -> TrustedRehearsalContext:
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
            "Rehearsal is stale because main advanced",
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
        or run_data.get("status") != expected_run_status
        or run_data.get("conclusion") != expected_run_conclusion
    ):
        raise DomainError(
            "Rehearsal workflow evidence does not match required trusted state",
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
    return _verify_trusted_rehearsal_value(
        repo,
        value,
        expected_run_status="in_progress",
        expected_run_conclusion=None,
    )


def resolve_trusted_selection_rehearsal(
    repo: str,
    payload: dict,
    repo_dir: Path,
) -> TrustedRehearsalContext | None:
    if payload.get("kind") != "operation_request" or payload.get("operation") != "experiment.decision":
        return None
    input_value = payload.get("input")
    if not isinstance(input_value, dict) or input_value.get("to_state") != "SELECTED":
        return None
    experiment_id = input_value.get("experiment_id")
    if not isinstance(experiment_id, str) or not experiment_id:
        raise DomainError("SELECTED decision experiment_id is invalid")

    try:
        state = json.loads(
            (repo_dir / f"experiments/{experiment_id}/state.json").read_text(encoding="utf-8")
        )
        rehearsal_id = state["current_rehearsal_id"]
        rehearsal = json.loads(
            (
                repo_dir
                / f"experiments/{experiment_id}/rehearsals/{rehearsal_id}.json"
            ).read_text(encoding="utf-8")
        )
    except Exception as exc:
        raise DomainError(
            f"cannot resolve current Rehearsal: {exc}",
            code="DOMAIN_PREREQUISITE_MISSING",
        ) from exc

    required_record = {
        "candidate_id",
        "experiment_id",
        "github_run_attempt",
        "github_run_id",
        "integration_sha",
        "integration_tree_sha",
        "kind",
        "main_sha",
        "policy_digest",
        "rehearsal_id",
        "rehearsal_ref",
        "scope_digest",
        "source_sha",
        "workflow_source_sha",
        "checks",
    }
    if set(rehearsal) != required_record or rehearsal.get("kind") != "rehearsal":
        raise DomainError(
            "current Rehearsal record shape is invalid",
            code="DOMAIN_BOUND_EXPERIMENT_INVALID",
        )
    value = {
        "experiment_id": rehearsal["experiment_id"],
        "candidate_id": rehearsal["candidate_id"],
        "rehearsal_id": rehearsal["rehearsal_id"],
        "main_sha": rehearsal["main_sha"],
        "source_sha": rehearsal["source_sha"],
        "integration_sha": rehearsal["integration_sha"],
        "integration_tree_sha": rehearsal["integration_tree_sha"],
        "rehearsal_ref": rehearsal["rehearsal_ref"],
        "workflow_source_sha": rehearsal["workflow_source_sha"],
        "run_id": rehearsal["github_run_id"],
        "run_attempt": rehearsal["github_run_attempt"],
        "policy_digest": rehearsal["policy_digest"],
        "scope_digest": rehearsal["scope_digest"],
        "checks": rehearsal["checks"],
    }
    return _verify_trusted_rehearsal_value(
        repo,
        value,
        expected_run_status="completed",
        expected_run_conclusion="success",
    )


def resolve_trusted_retention(
    repo: str,
    payload: dict,
    repo_dir: Path,
) -> TrustedRetentionContext | None:
    if payload.get("kind") != "operation_request" or payload.get("operation") != "experiment.decision":
        return None
    input_value = payload.get("input")
    if (
        not isinstance(input_value, dict)
        or input_value.get("to_state") not in {"PROMISING", "SELECTED"}
    ):
        return None
    experiment_id = input_value.get("experiment_id")
    if not isinstance(experiment_id, str) or not experiment_id:
        raise DomainError("promotion/selection decision experiment_id is invalid")

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


def resolve_trusted_integration(
    repo: str,
    payload: dict,
    *,
    authority: str,
    context_path: str | None,
) -> TrustedIntegrationContext | None:
    if payload.get("kind") != "operation_request" or payload.get("operation") != "integration.register":
        return None
    if authority != "integration":
        raise DomainError(
            "integration.register requires trusted integration authority",
            code="DOMAIN_AUTHORIZATION_FAILED",
        )
    if not context_path:
        raise DomainError(
            "trusted Integration context file is required",
            code="DOMAIN_AUTHORIZATION_FAILED",
        )
    try:
        value = json.loads(Path(context_path).read_text(encoding="utf-8"))
    except Exception as exc:
        raise DomainError(
            f"invalid trusted Integration context: {exc}",
            code="DOMAIN_AUTHORIZATION_FAILED",
        ) from exc
    required = {
        "experiment_id",
        "candidate_id",
        "rehearsal_id",
        "integration_id",
        "pr_number",
        "pr_id",
        "pr_url",
        "head_ref",
        "head_sha",
        "head_tree_sha",
        "merge_sha",
        "merge_tree_sha",
        "merged_at",
        "merged_by_login",
        "merged_by_user_id",
        "workflow_source_sha",
        "run_id",
        "run_attempt",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise DomainError(
            "trusted Integration context keys mismatch",
            code="DOMAIN_AUTHORIZATION_FAILED",
        )

    experiment_id = str(value["experiment_id"])
    match = re.fullmatch(r"EXP-([1-9][0-9]*)", experiment_id)
    if not match:
        raise DomainError("trusted Integration experiment_id is invalid")
    issue = match.group(1)
    pr_number = str(value["pr_number"])
    if not re.fullmatch(r"[1-9][0-9]*", pr_number):
        raise DomainError("trusted Integration pr_number is invalid")
    expected_id = f"I-{issue}-PR-{pr_number}"
    if value["integration_id"] != expected_id:
        raise DomainError(
            "trusted Integration id is not canonical",
            code="DOMAIN_INTEGRATION_CONFLICT",
        )
    expected_head_ref = f"game-exp/integration/{issue}/{value['rehearsal_id']}"
    if value["head_ref"] != expected_head_ref:
        raise DomainError(
            "trusted Integration head ref is not canonical",
            code="DOMAIN_INTEGRATION_CONFLICT",
        )

    pr = github_json(repo, f"/pulls/{urllib.parse.quote(pr_number, safe='')}")
    head = pr.get("head") or {}
    merged_by = pr.get("merged_by") or {}
    if (
        pr.get("merged") is not True
        or str(pr.get("id")) != str(value["pr_id"])
        or str(pr.get("html_url") or "") != str(value["pr_url"])
        or (pr.get("base") or {}).get("ref") != "main"
        or head.get("ref") != value["head_ref"]
        or head.get("sha") != value["head_sha"]
        or (head.get("repo") or {}).get("full_name") != repo
        or pr.get("merge_commit_sha") != value["merge_sha"]
        or pr.get("merged_at") != value["merged_at"]
        or merged_by.get("login") != value["merged_by_login"]
        or str(merged_by.get("id")) != str(value["merged_by_user_id"])
    ):
        raise DomainError(
            "live Integration PR evidence differs from trusted context",
            code="DOMAIN_INTEGRATION_CONFLICT",
        )

    head_commit = github_json(
        repo,
        f"/git/commits/{urllib.parse.quote(str(value['head_sha']), safe='')}",
    )
    head_tree = (head_commit.get("tree") or {}).get("sha")
    if head_tree != value["head_tree_sha"]:
        raise DomainError(
            "Integration PR head tree differs from trusted context",
            code="DOMAIN_INTEGRATION_CONFLICT",
        )

    merge_commit = github_json(
        repo,
        f"/git/commits/{urllib.parse.quote(str(value['merge_sha']), safe='')}",
    )
    merge_tree = (merge_commit.get("tree") or {}).get("sha")
    if merge_tree != value["merge_tree_sha"]:
        raise DomainError(
            "Integration merge tree differs from trusted context",
            code="DOMAIN_INTEGRATION_CONFLICT",
        )

    compare = github_json(
        repo,
        f"/compare/{urllib.parse.quote(str(value['merge_sha']), safe='')}...main",
    )
    merge_base = compare.get("merge_base_commit") or {}
    if compare.get("status") not in {"ahead", "identical"} or merge_base.get("sha") != value["merge_sha"]:
        raise DomainError(
            "Integration merge commit is not in current main history",
            code="DOMAIN_INTEGRATION_CONFLICT",
        )

    run_id = str(value["run_id"])
    run_attempt = str(value["run_attempt"])
    run_data = github_json(
        repo,
        f"/actions/runs/{urllib.parse.quote(run_id, safe='')}",
    )
    if (
        str(run_data.get("run_attempt")) != run_attempt
        or run_data.get("event") != "workflow_dispatch"
        or run_data.get("path") != ".github/workflows/game-exp-integration-finalize.yml"
        or run_data.get("head_sha") != value["workflow_source_sha"]
        or run_data.get("status") != "in_progress"
    ):
        raise DomainError(
            "Integration context is not from the active trusted finalize workflow",
            code="DOMAIN_INTEGRATION_CONFLICT",
        )

    return TrustedIntegrationContext(
        experiment_id=experiment_id,
        candidate_id=str(value["candidate_id"]),
        rehearsal_id=str(value["rehearsal_id"]),
        integration_id=str(value["integration_id"]),
        pr_number=pr_number,
        pr_id=str(value["pr_id"]),
        pr_url=str(value["pr_url"]),
        head_ref=str(value["head_ref"]),
        head_sha=str(value["head_sha"]),
        head_tree_sha=str(value["head_tree_sha"]),
        merge_sha=str(value["merge_sha"]),
        merge_tree_sha=str(value["merge_tree_sha"]),
        merged_at=str(value["merged_at"]),
        merged_by_login=str(value["merged_by_login"]),
        merged_by_user_id=str(value["merged_by_user_id"]),
        workflow_source_sha=str(value["workflow_source_sha"]),
        run_id=run_id,
        run_attempt=run_attempt,
    )


def resolve_trusted_actor(repo: str, payload: dict) -> TrustedActorContext | None:
    if payload.get("kind") != "operation_request":
        return None
    if payload.get("operation") not in {
        "experiment.decision",
        "review.record",
        "archive.prepare",
        "archive.abort",
    }:
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
    ap.add_argument("--authority", choices=("request", "candidate", "rehearsal", "integration", "archive"), default="request")
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
        trusted_selection_rehearsal = resolve_trusted_selection_rehearsal(
            args.repo,
            payload,
            repo_dir,
        )
        trusted_retention = resolve_trusted_retention(
            args.repo,
            payload,
            repo_dir,
        )
        trusted_integration = resolve_trusted_integration(
            args.repo,
            payload,
            authority=args.authority,
            context_path=args.trusted_context_json,
        )
        trusted_archive = resolve_trusted_archive(
            args.repo,
            payload,
            repo_dir,
            authority=args.authority,
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
            trusted_rehearsal=trusted_selection_rehearsal or trusted_rehearsal,
            trusted_integration=trusted_integration,
            trusted_archive=trusted_archive,
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
