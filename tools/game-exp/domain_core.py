from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from protocol_core import ProtocolError, digest_object

SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")
EXP_RE = re.compile(r"^EXP-[1-9][0-9]*$")
CANDIDATE_RE = re.compile(r"^C-([1-9][0-9]*)-([0-9]+)-([1-9][0-9]*)$")
REHEARSAL_RE = re.compile(r"^R-([1-9][0-9]*)-([0-9]+)-([1-9][0-9]*)$")
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class DomainError(ProtocolError):
    def __init__(self, message: str, *, code: str = "DOMAIN_INVALID"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class TrustedBindingContext:
    host: str
    repository_id: str
    issue_id: str
    issue_number: str
    parent_sha: str


@dataclass(frozen=True)
class TrustedActorContext:
    login: str
    user_id: str
    permission: str


@dataclass(frozen=True)
class TrustedCandidateContext:
    experiment_id: str
    candidate_id: str
    source_sha: str
    manifest_digest: str
    artifact_digest: str
    policy_digest: str
    workflow_source_sha: str
    run_id: str
    run_attempt: str
    checks: tuple[dict[str, Any], ...]
    retention: dict[str, Any]
    attestation: dict[str, Any]


@dataclass(frozen=True)
class TrustedRetentionContext:
    experiment_id: str
    candidate_id: str
    release_id: str
    release_tag: str
    release_url: str
    immutable: bool
    target_commitish: str
    artifact_name: str
    artifact_digest: str
    asset_id: str


@dataclass(frozen=True)
class TrustedRehearsalContext:
    experiment_id: str
    candidate_id: str
    rehearsal_id: str
    main_sha: str
    source_sha: str
    integration_sha: str
    integration_tree_sha: str
    rehearsal_ref: str
    workflow_source_sha: str
    run_id: str
    run_attempt: str
    policy_digest: str
    checks: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class DomainPlan:
    status: str
    experiment_id: str | None
    writes: dict[str, dict[str, Any]]

    @property
    def paths(self) -> list[str]:
        return sorted(self.writes)


def _expect_keys(
    obj: dict[str, Any],
    required: set[str],
    *,
    where: str,
    optional: set[str] | None = None,
) -> None:
    optional = optional or set()
    missing = required - obj.keys()
    extra = obj.keys() - required - optional
    if missing:
        raise DomainError(f"{where}: missing keys {sorted(missing)}")
    if extra:
        raise DomainError(f"{where}: unknown keys {sorted(extra)}")


def _mapping(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DomainError(f"{where}: expected object")
    return value


def _string(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DomainError(f"{where}: expected non-empty string")
    return value


def _canonical_decimal(value: Any, where: str) -> str:
    value = _string(value, where)
    if not value.isdigit() or (value.startswith("0") and value != "0"):
        raise DomainError(f"{where}: expected canonical decimal string")
    return value


def _string_list(value: Any, where: str, *, min_items: int = 0) -> list[str]:
    if not isinstance(value, list):
        raise DomainError(f"{where}: expected list")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise DomainError(f"{where}: expected non-empty strings")
    if len(value) < min_items:
        raise DomainError(f"{where}: requires at least {min_items} item(s)")
    return list(value)


def _require_nfc(value: str, where: str) -> None:
    if unicodedata.normalize("NFC", value) != value:
        raise DomainError(f"{where}: expected NFC")


def validate_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    _expect_keys(
        manifest,
        {
            "schema_version",
            "experiment",
            "title",
            "operation_id",
            "parent",
            "hypothesis",
            "success_criteria",
            "kill_criteria",
            "scope",
            "runtime",
            "review",
            "created_at",
        },
        where="manifest",
    )
    if manifest["schema_version"] != 1:
        raise DomainError("manifest.schema_version must equal 1")

    exp = _mapping(manifest["experiment"], "manifest.experiment")
    _expect_keys(
        exp,
        {"host", "repository_id", "issue_id", "issue_number"},
        where="manifest.experiment",
    )
    _string(exp["host"], "manifest.experiment.host")
    for name in ("repository_id", "issue_id", "issue_number"):
        _canonical_decimal(exp[name], f"manifest.experiment.{name}")

    _string(manifest["title"], "manifest.title")
    _string(manifest["operation_id"], "manifest.operation_id")

    parent = _mapping(manifest["parent"], "manifest.parent")
    _expect_keys(parent, {"experiment", "commit"}, where="manifest.parent")
    if parent["experiment"] is not None:
        parent_exp = _string(parent["experiment"], "manifest.parent.experiment")
        if not EXP_RE.fullmatch(parent_exp):
            raise DomainError("manifest.parent.experiment must be EXP-<number> or null")
    parent_sha = _string(parent["commit"], "manifest.parent.commit")
    if not SHA_RE.fullmatch(parent_sha):
        raise DomainError("manifest.parent.commit must be lowercase hex SHA")

    _string(manifest["hypothesis"], "manifest.hypothesis")
    _string_list(manifest["success_criteria"], "manifest.success_criteria", min_items=1)
    _string_list(manifest["kill_criteria"], "manifest.kill_criteria", min_items=1)

    scope = _mapping(manifest["scope"], "manifest.scope")
    _expect_keys(scope, {"allowed", "avoid"}, where="manifest.scope")
    patterns = _string_list(scope["allowed"], "manifest.scope.allowed")
    patterns += _string_list(scope["avoid"], "manifest.scope.avoid")
    for pattern in patterns:
        _require_nfc(pattern, "manifest scope pattern")

    runtime = _mapping(manifest["runtime"], "manifest.runtime")
    _expect_keys(runtime, {"godot", "export_templates", "addons_lock"}, where="manifest.runtime")
    for name in ("godot", "export_templates", "addons_lock"):
        _string(runtime[name], f"manifest.runtime.{name}")

    review = _mapping(manifest["review"], "manifest.review")
    _expect_keys(review, {"protocol"}, where="manifest.review")
    _string(review["protocol"], "manifest.review.protocol")
    _string(manifest["created_at"], "manifest.created_at")
    return manifest


def _binding_record(
    *,
    request_id: str,
    payload_digest: str,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    exp = manifest["experiment"]
    issue_number = exp["issue_number"]
    experiment_id = f"EXP-{issue_number}"
    manifest_digest = digest_object(manifest)
    initialization = {
        "branch_ref": f"refs/heads/exp/{issue_number}",
        "base_tag_ref": f"refs/tags/exp-base/{issue_number}",
        "final_tag_ref": f"refs/tags/exp-final/{issue_number}",
        "manifest_path": f"experiments/{experiment_id}/manifest.yaml",
        "manifest_snapshot_path": f"experiments/{experiment_id}/manifest.json",
        "manifest_digest": manifest_digest,
        "manifest_schema_version": manifest["schema_version"],
    }
    initialization["initialization_plan_digest"] = digest_object(initialization)
    return {
        "kind": "experiment_identity",
        "experiment_id": experiment_id,
        "request_id": request_id,
        "inputs_digest": payload_digest,
        "parent_sha": manifest["parent"]["commit"],
        "canonical": {
            "host": exp["host"],
            "repository_id": exp["repository_id"],
            "issue_id": exp["issue_id"],
            "issue_number": issue_number,
        },
        "initialization": initialization,
    }


def _state_record(experiment_id: str, request_id: str) -> dict[str, Any]:
    return {
        "kind": "experiment_state",
        "experiment_id": experiment_id,
        "lifecycle": "ACTIVE",
        "sequence": 0,
        "last_decision_id": None,
        "archive_lock": None,
        "created_by_request_id": request_id,
    }


DECISION_TRANSITIONS = {
    "ACTIVE": {"REVIEW"},
    "REVIEW": {"ACTIVE", "PROMISING", "REJECTED"},
    "PROMISING": {"ACTIVE", "SELECTED", "REJECTED"},
    "SELECTED": set(),
    "REJECTED": set(),
    "INTEGRATED": set(),
    "ARCHIVED": set(),
}


def _read_json_file(repo_dir: Path, path: str, *, where: str) -> dict[str, Any]:
    target = repo_dir / path
    if not target.exists():
        raise DomainError(f"{where}: missing authoritative file {path}", code="DOMAIN_BOUND_EXPERIMENT_INVALID")
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        raise DomainError(
            f"{where}: invalid JSON in {path}: {exc}",
            code="DOMAIN_BOUND_EXPERIMENT_INVALID",
        ) from exc
    if not isinstance(value, dict):
        raise DomainError(f"{where}: {path} must be an object", code="DOMAIN_BOUND_EXPERIMENT_INVALID")
    return value


def _expected_domain_paths(experiment_id: str) -> list[str]:
    root = f"experiments/{experiment_id}"
    return sorted([f"{root}/binding.json", f"{root}/manifest.json", f"{root}/state.json"])


def _load_bound_experiment(repo_dir: Path, experiment_id: str):
    if not EXP_RE.fullmatch(experiment_id):
        raise DomainError("experiment_id must be EXP-<number>")
    issue_number = experiment_id.removeprefix("EXP-")
    root = f"experiments/{experiment_id}"
    binding = _read_json_file(repo_dir, f"{root}/binding.json", where=experiment_id)
    manifest = _read_json_file(repo_dir, f"{root}/manifest.json", where=experiment_id)
    state = _read_json_file(repo_dir, f"{root}/state.json", where=experiment_id)

    if binding.get("kind") != "experiment_identity" or binding.get("experiment_id") != experiment_id:
        raise DomainError("binding identity mismatch", code="DOMAIN_BOUND_EXPERIMENT_INVALID")
    if state.get("kind") != "experiment_state" or state.get("experiment_id") != experiment_id:
        raise DomainError("state identity mismatch", code="DOMAIN_BOUND_EXPERIMENT_INVALID")

    request_id = binding.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        raise DomainError("binding request_id missing", code="DOMAIN_BOUND_EXPERIMENT_INVALID")
    operation = _read_json_file(
        repo_dir,
        f"operations/{request_id}.json",
        where=experiment_id,
    )
    payload = operation.get("payload")
    stored_digest = operation.get("payload_digest")
    if not isinstance(payload, dict) or not isinstance(stored_digest, str):
        raise DomainError("binding operation record incomplete", code="DOMAIN_BOUND_EXPERIMENT_INVALID")
    actual_digest = digest_object(payload)
    if actual_digest != stored_digest:
        raise DomainError(
            f"binding operation payload digest mismatch: stored={stored_digest} actual={actual_digest}",
            code="DOMAIN_BOUND_EXPERIMENT_INVALID",
        )
    if binding.get("inputs_digest") != stored_digest:
        raise DomainError("binding inputs_digest mismatch", code="DOMAIN_BOUND_EXPERIMENT_INVALID")
    if operation.get("domain_status") != "APPLIED" or operation.get("domain_experiment_id") != experiment_id:
        raise DomainError("binding operation was not applied", code="DOMAIN_BOUND_EXPERIMENT_INVALID")
    if sorted(operation.get("domain_paths") or []) != _expected_domain_paths(experiment_id):
        raise DomainError("binding operation domain_paths mismatch", code="DOMAIN_BOUND_EXPERIMENT_INVALID")
    if payload.get("kind") != "operation_request" or payload.get("operation") != "experiment.bind":
        raise DomainError("binding operation payload type mismatch", code="DOMAIN_BOUND_EXPERIMENT_INVALID")
    input_value = payload.get("input")
    if not isinstance(input_value, dict) or input_value.get("manifest") != manifest:
        raise DomainError("manifest snapshot differs from binding request", code="DOMAIN_BOUND_EXPERIMENT_INVALID")

    init = binding.get("initialization")
    if not isinstance(init, dict) or init.get("manifest_digest") != digest_object(manifest):
        raise DomainError("binding manifest digest mismatch", code="DOMAIN_BOUND_EXPERIMENT_INVALID")
    canonical = binding.get("canonical")
    if not isinstance(canonical, dict) or canonical.get("issue_number") != issue_number:
        raise DomainError("binding canonical issue mismatch", code="DOMAIN_BOUND_EXPERIMENT_INVALID")
    if manifest.get("experiment", {}).get("issue_number") != issue_number:
        raise DomainError("manifest issue mismatch", code="DOMAIN_BOUND_EXPERIMENT_INVALID")
    if manifest.get("parent", {}).get("commit") != binding.get("parent_sha"):
        raise DomainError("binding parent mismatch", code="DOMAIN_BOUND_EXPERIMENT_INVALID")
    if state.get("created_by_request_id") != request_id:
        raise DomainError("state binding origin mismatch", code="DOMAIN_BOUND_EXPERIMENT_INVALID")

    lifecycle = state.get("lifecycle")
    if lifecycle not in DECISION_TRANSITIONS:
        raise DomainError("unknown lifecycle state", code="DOMAIN_BOUND_EXPERIMENT_INVALID")
    sequence = state.get("sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
        raise DomainError("invalid state sequence", code="DOMAIN_BOUND_EXPERIMENT_INVALID")
    last_decision_id = state.get("last_decision_id")
    if last_decision_id is not None and (not isinstance(last_decision_id, str) or not last_decision_id):
        raise DomainError("invalid last_decision_id", code="DOMAIN_BOUND_EXPERIMENT_INVALID")
    return binding, manifest, state, operation


def _plan_decision(
    *,
    repo_dir: Path,
    payload: dict[str, Any],
    request_id: str,
    trusted_actor: TrustedActorContext | None,
    trusted_retention: TrustedRetentionContext | None,
) -> DomainPlan:
    input_value = _mapping(payload.get("input"), "operation.input")
    _expect_keys(
        input_value,
        {"experiment_id", "to_state", "previous_decision_id", "reason"},
        where="operation.input",
    )
    experiment_id = _string(input_value["experiment_id"], "operation.input.experiment_id")
    to_state = _string(input_value["to_state"], "operation.input.to_state")
    reason = _string(input_value["reason"], "operation.input.reason")
    previous_decision_id = input_value["previous_decision_id"]
    if previous_decision_id is not None:
        previous_decision_id = _string(
            previous_decision_id,
            "operation.input.previous_decision_id",
        )

    if trusted_actor is None:
        raise DomainError(
            "trusted actor context is required for lifecycle decisions",
            code="DOMAIN_AUTHORIZATION_FAILED",
        )
    if trusted_actor.permission not in {"admin", "maintain", "write"}:
        raise DomainError(
            f"actor {trusted_actor.login!r} lacks write permission",
            code="DOMAIN_AUTHORIZATION_FAILED",
        )

    _binding, _manifest, state, _binding_operation = _load_bound_experiment(
        repo_dir,
        experiment_id,
    )
    if state.get("archive_lock") is not None:
        raise DomainError(
            "decision rejected while archive mutation lock is active",
            code="DOMAIN_DECISION_CONFLICT",
        )
    current = state["lifecycle"]
    current_last = state.get("last_decision_id")
    if previous_decision_id != current_last:
        raise DomainError(
            f"previous_decision_id mismatch: expected {current_last!r}, got {previous_decision_id!r}",
            code="DOMAIN_DECISION_CONFLICT",
        )
    if to_state in {"ARCHIVED", "INTEGRATED"}:
        raise DomainError(
            f"{to_state} is reserved for its dedicated trusted operation",
            code="DOMAIN_INVALID_TRANSITION",
        )
    if to_state not in DECISION_TRANSITIONS[current]:
        raise DomainError(
            f"invalid lifecycle transition {current} -> {to_state}",
            code="DOMAIN_INVALID_TRANSITION",
        )
    promotion_evidence = None
    if to_state == "PROMISING":
        candidate_id = state.get("current_candidate_id")
        review_id = state.get("current_review_id")
        review_candidate_id = state.get("current_review_candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise DomainError(
                "PROMISING requires a current Candidate",
                code="DOMAIN_PREREQUISITE_MISSING",
            )
        if (
            not isinstance(review_id, str)
            or not review_id
            or review_candidate_id != candidate_id
        ):
            raise DomainError(
                "PROMISING requires a current Review bound to the current Candidate",
                code="DOMAIN_PREREQUISITE_MISSING",
            )

        candidate = _load_candidate(repo_dir, experiment_id, candidate_id)
        review = _read_json_file(
            repo_dir,
            f"experiments/{experiment_id}/reviews/{review_id}.json",
            where=experiment_id,
        )
        if (
            review.get("kind") != "review"
            or review.get("review_id") != review_id
            or review.get("experiment_id") != experiment_id
            or review.get("candidate_id") != candidate_id
            or review.get("artifact_digest") != candidate.get("artifact_digest")
            or review.get("outcome") != "PASS"
        ):
            raise DomainError(
                "PROMISING requires a current PASS Review for the current Candidate",
                code="DOMAIN_PREREQUISITE_MISSING",
            )

        retention = candidate.get("retention")
        if not isinstance(retention, dict):
            raise DomainError(
                "PROMISING requires Candidate retention",
                code="DOMAIN_PREREQUISITE_MISSING",
            )
        if trusted_retention is None:
            raise DomainError(
                "PROMISING requires current trusted Level-3 retention verification",
                code="DOMAIN_PREREQUISITE_MISSING",
            )
        expected_retention = {
            "experiment_id": experiment_id,
            "candidate_id": candidate_id,
            "release_tag": retention.get("release_tag"),
            "artifact_digest": candidate.get("artifact_digest"),
            "target_commitish": candidate.get("source_sha"),
        }
        actual_retention = {
            "experiment_id": trusted_retention.experiment_id,
            "candidate_id": trusted_retention.candidate_id,
            "release_tag": trusted_retention.release_tag,
            "artifact_digest": trusted_retention.artifact_digest,
            "target_commitish": trusted_retention.target_commitish,
        }
        if actual_retention != expected_retention:
            raise DomainError(
                "current trusted retention evidence does not match Candidate",
                code="DOMAIN_PREREQUISITE_MISSING",
            )
        if (
            trusted_retention.immutable is not True
            or trusted_retention.artifact_name != "candidate.tgz"
            or not trusted_retention.release_id
            or not trusted_retention.asset_id
        ):
            raise DomainError(
                "current trusted retention evidence is incomplete or mutable",
                code="DOMAIN_PREREQUISITE_MISSING",
            )
        promotion_evidence = {
            "candidate_id": candidate_id,
            "review_id": review_id,
            "artifact_digest": candidate["artifact_digest"],
            "source_sha": candidate["source_sha"],
            "retention": {
                "provider": "github-immutable-release",
                "release_id": trusted_retention.release_id,
                "release_tag": trusted_retention.release_tag,
                "release_url": trusted_retention.release_url,
                "asset_id": trusted_retention.asset_id,
                "asset_name": trusted_retention.artifact_name,
                "artifact_digest": trusted_retention.artifact_digest,
                "immutable": True,
            },
        }
    elif to_state == "SELECTED":
        raise DomainError(
            "SELECTED requires a current trusted Rehearsal; Rehearsal semantics are not active yet",
            code="DOMAIN_PREREQUISITE_MISSING",
        )

    decision_path = f"experiments/{experiment_id}/decisions/{request_id}.json"
    if (repo_dir / decision_path).exists():
        raise DomainError("decision_id already exists", code="DOMAIN_DECISION_CONFLICT")

    next_sequence = state["sequence"] + 1
    decision = {
        "kind": "decision_event",
        "decision_id": request_id,
        "experiment_id": experiment_id,
        "sequence": next_sequence,
        "previous_decision_id": previous_decision_id,
        "from_state": current,
        "to_state": to_state,
        "reason": reason,
        "actor": {
            "login": trusted_actor.login,
            "user_id": trusted_actor.user_id,
            "permission": trusted_actor.permission,
            "source": "github-collaborator-permission",
        },
    }
    actor_claim = payload.get("actor_claim")
    if actor_claim is not None:
        decision["actor_claim"] = actor_claim
    if promotion_evidence is not None:
        decision["promotion_evidence"] = promotion_evidence

    next_state = dict(state)
    next_state["lifecycle"] = to_state
    next_state["sequence"] = next_sequence
    next_state["last_decision_id"] = request_id

    return DomainPlan(
        status="APPLIED",
        experiment_id=experiment_id,
        writes={
            decision_path: decision,
            f"experiments/{experiment_id}/state.json": next_state,
        },
    )


def _load_candidate(
    repo_dir: Path,
    experiment_id: str,
    candidate_id: str,
) -> dict[str, Any]:
    match = CANDIDATE_RE.fullmatch(candidate_id)
    if not match or match.group(1) != experiment_id.removeprefix("EXP-"):
        raise DomainError("candidate_id does not match experiment", code="DOMAIN_REVIEW_CONFLICT")
    candidate = _read_json_file(
        repo_dir,
        f"experiments/{experiment_id}/candidates/{candidate_id}.json",
        where=experiment_id,
    )
    if (
        candidate.get("kind") != "candidate"
        or candidate.get("candidate_id") != candidate_id
        or candidate.get("experiment_id") != experiment_id
    ):
        raise DomainError("Candidate identity mismatch", code="DOMAIN_REVIEW_CONFLICT")

    artifact_digest = candidate.get("artifact_digest")
    manifest_digest = candidate.get("manifest_digest")
    source_sha = candidate.get("source_sha")
    if not isinstance(artifact_digest, str) or not SHA256_RE.fullmatch(artifact_digest):
        raise DomainError("Candidate artifact digest invalid", code="DOMAIN_REVIEW_CONFLICT")
    if not isinstance(manifest_digest, str) or not SHA256_RE.fullmatch(manifest_digest):
        raise DomainError("Candidate manifest digest invalid", code="DOMAIN_REVIEW_CONFLICT")
    if not isinstance(source_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise DomainError("Candidate source SHA invalid", code="DOMAIN_REVIEW_CONFLICT")

    checks = candidate.get("checks")
    if not isinstance(checks, list) or not checks:
        raise DomainError("Candidate trusted checks missing", code="DOMAIN_REVIEW_CONFLICT")
    seen: set[str] = set()
    for check in checks:
        if not isinstance(check, dict):
            raise DomainError("Candidate check invalid", code="DOMAIN_REVIEW_CONFLICT")
        if set(check) != {"name", "status", "source"}:
            raise DomainError("Candidate check keys invalid", code="DOMAIN_REVIEW_CONFLICT")
        name = check.get("name")
        if not isinstance(name, str) or not name or name in seen:
            raise DomainError("Candidate check name invalid", code="DOMAIN_REVIEW_CONFLICT")
        seen.add(name)
        if check.get("status") != "PASS" or check.get("source") != "TRUSTED_OBSERVED":
            raise DomainError("Candidate has non-trusted or failed check", code="DOMAIN_REVIEW_CONFLICT")

    retention = candidate.get("retention")
    if not isinstance(retention, dict):
        raise DomainError("Candidate retention missing", code="DOMAIN_REVIEW_CONFLICT")
    if (
        retention.get("provider") != "github-immutable-release"
        or retention.get("immutable") is not True
        or retention.get("artifact_digest") != artifact_digest
    ):
        raise DomainError("Candidate retention invalid", code="DOMAIN_REVIEW_CONFLICT")

    attestation = candidate.get("attestation")
    if not isinstance(attestation, dict):
        raise DomainError("Candidate attestation missing", code="DOMAIN_REVIEW_CONFLICT")
    if (
        attestation.get("provider") != "github-artifact-attestations"
        or attestation.get("verified") is not True
        or attestation.get("subject_digest") != artifact_digest
        or attestation.get("source_sha") != source_sha
    ):
        raise DomainError("Candidate attestation invalid", code="DOMAIN_REVIEW_CONFLICT")
    return candidate


def _plan_review(
    *,
    repo_dir: Path,
    payload: dict[str, Any],
    request_id: str,
    trusted_actor: TrustedActorContext | None,
) -> DomainPlan:
    input_value = _mapping(payload.get("input"), "operation.input")
    _expect_keys(
        input_value,
        {"experiment_id", "candidate_id", "outcome", "notes"},
        where="operation.input",
    )
    experiment_id = _string(input_value["experiment_id"], "operation.input.experiment_id")
    candidate_id = _string(input_value["candidate_id"], "operation.input.candidate_id")
    outcome = _string(input_value["outcome"], "operation.input.outcome")
    notes = _string(input_value["notes"], "operation.input.notes")
    if outcome not in {"PASS", "FAIL"}:
        raise DomainError("review outcome must be PASS or FAIL")

    if trusted_actor is None:
        raise DomainError(
            "trusted actor context is required for Review",
            code="DOMAIN_AUTHORIZATION_FAILED",
        )
    if trusted_actor.permission not in {"admin", "maintain", "write"}:
        raise DomainError(
            f"actor {trusted_actor.login!r} lacks write permission",
            code="DOMAIN_AUTHORIZATION_FAILED",
        )

    binding, manifest, state, _binding_operation = _load_bound_experiment(
        repo_dir,
        experiment_id,
    )
    if state.get("lifecycle") != "REVIEW":
        raise DomainError(
            f"Review requires REVIEW lifecycle, got {state.get('lifecycle')!r}",
            code="DOMAIN_REVIEW_CONFLICT",
        )
    if state.get("current_candidate_id") != candidate_id:
        raise DomainError(
            "Review must bind the current Candidate",
            code="DOMAIN_REVIEW_CONFLICT",
        )
    candidate = _load_candidate(repo_dir, experiment_id, candidate_id)
    binding_manifest_digest = binding.get("initialization", {}).get("manifest_digest")
    if candidate.get("manifest_digest") != binding_manifest_digest:
        raise DomainError(
            "Candidate manifest digest differs from authoritative binding",
            code="DOMAIN_REVIEW_CONFLICT",
        )

    protocol = manifest.get("review", {}).get("protocol")
    if not isinstance(protocol, str) or not protocol:
        raise DomainError("manifest review protocol missing", code="DOMAIN_BOUND_EXPERIMENT_INVALID")

    review_path = f"experiments/{experiment_id}/reviews/{request_id}.json"
    if (repo_dir / review_path).exists():
        raise DomainError("review_id already exists", code="DOMAIN_REVIEW_CONFLICT")

    review = {
        "kind": "review",
        "review_id": request_id,
        "experiment_id": experiment_id,
        "candidate_id": candidate_id,
        "artifact_digest": candidate["artifact_digest"],
        "protocol": protocol,
        "outcome": outcome,
        "notes": notes,
        "actor": {
            "login": trusted_actor.login,
            "user_id": trusted_actor.user_id,
            "permission": trusted_actor.permission,
            "source": "github-collaborator-permission",
        },
    }
    actor_claim = payload.get("actor_claim")
    if actor_claim is not None:
        review["actor_claim"] = actor_claim

    next_state = dict(state)
    review_sequence = next_state.get("review_sequence", 0)
    if not isinstance(review_sequence, int) or isinstance(review_sequence, bool) or review_sequence < 0:
        raise DomainError("invalid review_sequence", code="DOMAIN_BOUND_EXPERIMENT_INVALID")
    next_state["review_sequence"] = review_sequence + 1
    next_state["current_review_id"] = request_id
    next_state["current_review_candidate_id"] = candidate_id

    return DomainPlan(
        status="APPLIED",
        experiment_id=experiment_id,
        writes={
            review_path: review,
            f"experiments/{experiment_id}/state.json": next_state,
        },
    )


def _plan_candidate(
    *,
    repo_dir: Path,
    payload: dict[str, Any],
    trusted_candidate: TrustedCandidateContext | None,
) -> DomainPlan:
    input_value = _mapping(payload.get("input"), "operation.input")
    _expect_keys(input_value, {"experiment_id"}, where="operation.input")
    experiment_id = _string(input_value["experiment_id"], "operation.input.experiment_id")
    if trusted_candidate is None:
        raise DomainError(
            "trusted Candidate context is required",
            code="DOMAIN_AUTHORIZATION_FAILED",
        )
    if trusted_candidate.experiment_id != experiment_id:
        raise DomainError(
            "trusted Candidate experiment identity mismatch",
            code="DOMAIN_CANDIDATE_CONFLICT",
        )

    binding, _manifest, state, _binding_operation = _load_bound_experiment(
        repo_dir,
        experiment_id,
    )
    if state["lifecycle"] not in {"ACTIVE", "REVIEW"}:
        raise DomainError(
            f"cannot register Candidate while lifecycle is {state['lifecycle']}",
            code="DOMAIN_CANDIDATE_CONFLICT",
        )

    candidate_match = CANDIDATE_RE.fullmatch(trusted_candidate.candidate_id)
    if not candidate_match:
        raise DomainError("invalid trusted candidate_id")
    issue_number = experiment_id.removeprefix("EXP-")
    if candidate_match.group(1) != issue_number:
        raise DomainError(
            "candidate_id does not match experiment issue number",
            code="DOMAIN_CANDIDATE_CONFLICT",
        )
    if candidate_match.group(2) != trusted_candidate.run_id:
        raise DomainError("candidate_id run_id mismatch", code="DOMAIN_CANDIDATE_CONFLICT")
    if candidate_match.group(3) != trusted_candidate.run_attempt:
        raise DomainError(
            "candidate_id run_attempt mismatch",
            code="DOMAIN_CANDIDATE_CONFLICT",
        )

    if not re.fullmatch(r"[0-9a-f]{40}", trusted_candidate.source_sha):
        raise DomainError("Candidate source_sha must be a 40-character commit SHA")
    init = binding.get("initialization")
    if not isinstance(init, dict):
        raise DomainError("binding initialization missing", code="DOMAIN_BOUND_EXPERIMENT_INVALID")
    if trusted_candidate.manifest_digest != init.get("manifest_digest"):
        raise DomainError(
            "Candidate manifest digest does not match authoritative binding",
            code="DOMAIN_CANDIDATE_CONFLICT",
        )
    for name, value in (
        ("artifact_digest", trusted_candidate.artifact_digest),
        ("policy_digest", trusted_candidate.policy_digest),
    ):
        if not SHA256_RE.fullmatch(value):
            raise DomainError(f"Candidate {name} must be sha256:<64 lowercase hex>")
    if not re.fullmatch(r"[0-9]+", trusted_candidate.run_id):
        raise DomainError("Candidate run_id must be a decimal string")
    if not re.fullmatch(r"[1-9][0-9]*", trusted_candidate.run_attempt):
        raise DomainError("Candidate run_attempt must be a positive decimal string")
    if not re.fullmatch(r"[0-9a-f]{40}", trusted_candidate.workflow_source_sha):
        raise DomainError("Candidate workflow_source_sha must be a 40-character commit SHA")

    checks = list(trusted_candidate.checks)
    if not checks:
        raise DomainError("Candidate requires trusted checks")
    seen_checks: set[str] = set()
    normalized_checks: list[dict[str, str]] = []
    for index, check in enumerate(checks):
        if not isinstance(check, dict):
            raise DomainError(f"Candidate check {index} must be an object")
        _expect_keys(check, {"name", "status", "source"}, where=f"Candidate check {index}")
        name = _string(check["name"], f"Candidate check {index}.name")
        if name in seen_checks:
            raise DomainError("Candidate check names must be unique")
        seen_checks.add(name)
        if check["status"] != "PASS" or check["source"] != "TRUSTED_OBSERVED":
            raise DomainError(
                f"Candidate check {name!r} is not trusted PASS",
                code="DOMAIN_PREREQUISITE_MISSING",
            )
        normalized_checks.append(
            {"name": name, "status": "PASS", "source": "TRUSTED_OBSERVED"}
        )

    retention = dict(trusted_candidate.retention)
    _expect_keys(
        retention,
        {"provider", "release_tag", "release_url", "immutable", "artifact_digest"},
        where="Candidate retention",
    )
    if retention["provider"] != "github-immutable-release" or retention["immutable"] is not True:
        raise DomainError(
            "Candidate retention is not immutable",
            code="DOMAIN_PREREQUISITE_MISSING",
        )
    if retention["artifact_digest"] != trusted_candidate.artifact_digest:
        raise DomainError("Candidate retention digest mismatch", code="DOMAIN_CANDIDATE_CONFLICT")
    _string(retention["release_tag"], "Candidate retention.release_tag")
    _string(retention["release_url"], "Candidate retention.release_url")

    attestation = dict(trusted_candidate.attestation)
    _expect_keys(
        attestation,
        {"provider", "verified", "subject_digest", "source_sha"},
        where="Candidate attestation",
    )
    if attestation["provider"] != "github-artifact-attestations" or attestation["verified"] is not True:
        raise DomainError(
            "Candidate attestation is not verified",
            code="DOMAIN_PREREQUISITE_MISSING",
        )
    if attestation["subject_digest"] != trusted_candidate.artifact_digest:
        raise DomainError("Candidate attestation digest mismatch", code="DOMAIN_CANDIDATE_CONFLICT")
    if attestation["source_sha"] != trusted_candidate.source_sha:
        raise DomainError("Candidate attestation source mismatch", code="DOMAIN_CANDIDATE_CONFLICT")

    candidate_path = (
        f"experiments/{experiment_id}/candidates/{trusted_candidate.candidate_id}.json"
    )
    if (repo_dir / candidate_path).exists():
        raise DomainError("candidate_id already exists", code="DOMAIN_CANDIDATE_CONFLICT")

    candidate = {
        "kind": "candidate",
        "candidate_id": trusted_candidate.candidate_id,
        "experiment_id": experiment_id,
        "source_sha": trusted_candidate.source_sha,
        "manifest_digest": trusted_candidate.manifest_digest,
        "artifact_digest": trusted_candidate.artifact_digest,
        "policy_digest": trusted_candidate.policy_digest,
        "workflow_source_sha": trusted_candidate.workflow_source_sha,
        "github_run_id": trusted_candidate.run_id,
        "github_run_attempt": trusted_candidate.run_attempt,
        "checks": normalized_checks,
        "retention": retention,
        "attestation": attestation,
    }
    next_state = dict(state)
    current_seq = next_state.get("candidate_sequence", 0)
    if not isinstance(current_seq, int) or isinstance(current_seq, bool) or current_seq < 0:
        raise DomainError("invalid candidate_sequence", code="DOMAIN_BOUND_EXPERIMENT_INVALID")
    next_state["candidate_sequence"] = current_seq + 1
    next_state["current_candidate_id"] = trusted_candidate.candidate_id
    next_state.pop("current_review_id", None)
    next_state.pop("current_review_candidate_id", None)

    return DomainPlan(
        status="APPLIED",
        experiment_id=experiment_id,
        writes={
            candidate_path: candidate,
            f"experiments/{experiment_id}/state.json": next_state,
        },
    )


def _plan_rehearsal(
    *,
    repo_dir: Path,
    payload: dict[str, Any],
    trusted_rehearsal: TrustedRehearsalContext | None,
) -> DomainPlan:
    input_value = _mapping(payload.get("input"), "operation.input")
    _expect_keys(input_value, {"experiment_id"}, where="operation.input")
    experiment_id = _string(input_value["experiment_id"], "operation.input.experiment_id")
    if trusted_rehearsal is None:
        raise DomainError(
            "trusted Rehearsal context is required",
            code="DOMAIN_AUTHORIZATION_FAILED",
        )
    if trusted_rehearsal.experiment_id != experiment_id:
        raise DomainError(
            "trusted Rehearsal experiment identity mismatch",
            code="DOMAIN_REHEARSAL_CONFLICT",
        )

    _binding, _manifest, state, _binding_operation = _load_bound_experiment(
        repo_dir,
        experiment_id,
    )
    if state.get("lifecycle") != "PROMISING":
        raise DomainError(
            f"Rehearsal requires PROMISING lifecycle, got {state.get('lifecycle')!r}",
            code="DOMAIN_INVALID_TRANSITION",
        )
    candidate_id = state.get("current_candidate_id")
    if not isinstance(candidate_id, str) or candidate_id != trusted_rehearsal.candidate_id:
        raise DomainError(
            "Rehearsal must bind the current Candidate",
            code="DOMAIN_REHEARSAL_CONFLICT",
        )
    candidate = _load_candidate(repo_dir, experiment_id, candidate_id)
    if candidate.get("source_sha") != trusted_rehearsal.source_sha:
        raise DomainError(
            "Rehearsal source SHA differs from current Candidate",
            code="DOMAIN_REHEARSAL_CONFLICT",
        )

    match = REHEARSAL_RE.fullmatch(trusted_rehearsal.rehearsal_id)
    issue_number = experiment_id.removeprefix("EXP-")
    if (
        not match
        or match.group(1) != issue_number
        or match.group(2) != trusted_rehearsal.run_id
        or match.group(3) != trusted_rehearsal.run_attempt
    ):
        raise DomainError(
            "Rehearsal id does not bind experiment/run identity",
            code="DOMAIN_REHEARSAL_CONFLICT",
        )
    expected_ref = (
        f"refs/tags/exp-rehearsal/{issue_number}/{trusted_rehearsal.rehearsal_id}"
    )
    if trusted_rehearsal.rehearsal_ref != expected_ref:
        raise DomainError(
            "Rehearsal ref is not canonical",
            code="DOMAIN_REHEARSAL_CONFLICT",
        )

    for name, value in (
        ("main_sha", trusted_rehearsal.main_sha),
        ("source_sha", trusted_rehearsal.source_sha),
        ("integration_sha", trusted_rehearsal.integration_sha),
        ("integration_tree_sha", trusted_rehearsal.integration_tree_sha),
        ("workflow_source_sha", trusted_rehearsal.workflow_source_sha),
    ):
        if not re.fullmatch(r"[0-9a-f]{40}", value):
            raise DomainError(f"Rehearsal {name} must be a 40-character SHA")
    if not SHA256_RE.fullmatch(trusted_rehearsal.policy_digest):
        raise DomainError("Rehearsal policy_digest must be sha256:<64 lowercase hex>")
    if not re.fullmatch(r"[0-9]+", trusted_rehearsal.run_id):
        raise DomainError("Rehearsal run_id must be a decimal string")
    if not re.fullmatch(r"[1-9][0-9]*", trusted_rehearsal.run_attempt):
        raise DomainError("Rehearsal run_attempt must be a positive decimal string")
    if trusted_rehearsal.integration_sha in {
        trusted_rehearsal.main_sha,
        trusted_rehearsal.source_sha,
    }:
        raise DomainError(
            "Rehearsal integration commit must be a two-parent integration object",
            code="DOMAIN_REHEARSAL_CONFLICT",
        )

    checks = list(trusted_rehearsal.checks)
    if not checks:
        raise DomainError(
            "Rehearsal requires trusted checks",
            code="DOMAIN_PREREQUISITE_MISSING",
        )
    normalized_checks: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, check in enumerate(checks):
        if not isinstance(check, dict):
            raise DomainError(f"Rehearsal check {index} must be an object")
        _expect_keys(check, {"name", "status", "source"}, where=f"Rehearsal check {index}")
        name = _string(check["name"], f"Rehearsal check {index}.name")
        if name in seen:
            raise DomainError("Rehearsal check names must be unique")
        seen.add(name)
        if check["status"] != "PASS" or check["source"] != "TRUSTED_OBSERVED":
            raise DomainError(
                f"Rehearsal check {name!r} is not trusted PASS",
                code="DOMAIN_PREREQUISITE_MISSING",
            )
        normalized_checks.append(
            {"name": name, "status": "PASS", "source": "TRUSTED_OBSERVED"}
        )

    rehearsal_path = (
        f"experiments/{experiment_id}/rehearsals/{trusted_rehearsal.rehearsal_id}.json"
    )
    if (repo_dir / rehearsal_path).exists():
        raise DomainError(
            "rehearsal_id already exists",
            code="DOMAIN_REHEARSAL_CONFLICT",
        )

    rehearsal = {
        "kind": "rehearsal",
        "rehearsal_id": trusted_rehearsal.rehearsal_id,
        "experiment_id": experiment_id,
        "candidate_id": candidate_id,
        "main_sha": trusted_rehearsal.main_sha,
        "source_sha": trusted_rehearsal.source_sha,
        "integration_sha": trusted_rehearsal.integration_sha,
        "integration_tree_sha": trusted_rehearsal.integration_tree_sha,
        "rehearsal_ref": trusted_rehearsal.rehearsal_ref,
        "workflow_source_sha": trusted_rehearsal.workflow_source_sha,
        "github_run_id": trusted_rehearsal.run_id,
        "github_run_attempt": trusted_rehearsal.run_attempt,
        "policy_digest": trusted_rehearsal.policy_digest,
        "checks": normalized_checks,
    }
    next_state = dict(state)
    seq = next_state.get("rehearsal_sequence", 0)
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
        raise DomainError(
            "invalid rehearsal_sequence",
            code="DOMAIN_BOUND_EXPERIMENT_INVALID",
        )
    next_state["rehearsal_sequence"] = seq + 1
    next_state["current_rehearsal_id"] = trusted_rehearsal.rehearsal_id
    next_state["current_rehearsal_candidate_id"] = candidate_id
    next_state["current_rehearsal_main_sha"] = trusted_rehearsal.main_sha

    return DomainPlan(
        status="APPLIED",
        experiment_id=experiment_id,
        writes={
            rehearsal_path: rehearsal,
            f"experiments/{experiment_id}/state.json": next_state,
        },
    )


def plan_domain_mutation(
    *,
    repo_dir: Path,
    payload: dict[str, Any],
    request_id: str,
    payload_digest: str,
    repository_full_name: str,
    trusted_binding: TrustedBindingContext | None = None,
    trusted_actor: TrustedActorContext | None = None,
    trusted_candidate: TrustedCandidateContext | None = None,
    trusted_retention: TrustedRetentionContext | None = None,
    trusted_rehearsal: TrustedRehearsalContext | None = None,
) -> DomainPlan:
    if payload.get("kind") != "operation_request":
        return DomainPlan(status="REQUEST_ONLY", experiment_id=None, writes={})
    operation = payload.get("operation")
    if operation == "rehearsal.register":
        return _plan_rehearsal(
            repo_dir=repo_dir,
            payload=payload,
            trusted_rehearsal=trusted_rehearsal,
        )
    if operation == "review.record":
        return _plan_review(
            repo_dir=repo_dir,
            payload=payload,
            request_id=request_id,
            trusted_actor=trusted_actor,
        )
    if operation == "candidate.register":
        return _plan_candidate(
            repo_dir=repo_dir,
            payload=payload,
            trusted_candidate=trusted_candidate,
        )
    if operation == "experiment.decision":
        return _plan_decision(
            repo_dir=repo_dir,
            payload=payload,
            request_id=request_id,
            trusted_actor=trusted_actor,
            trusted_retention=trusted_retention,
        )
    if operation != "experiment.bind":
        return DomainPlan(status="REQUEST_ONLY", experiment_id=None, writes={})

    input_value = _mapping(payload.get("input"), "operation.input")
    _expect_keys(input_value, {"manifest"}, where="operation.input")
    manifest = validate_manifest(_mapping(input_value["manifest"], "operation.input.manifest"))

    if manifest["operation_id"] != request_id:
        raise DomainError("manifest.operation_id must equal request_id")

    if trusted_binding is None:
        raise DomainError("trusted binding context is required")

    exp = manifest["experiment"]
    expected = {
        "host": trusted_binding.host,
        "repository_id": trusted_binding.repository_id,
        "issue_id": trusted_binding.issue_id,
        "issue_number": trusted_binding.issue_number,
    }
    actual = {
        "host": exp["host"],
        "repository_id": exp["repository_id"],
        "issue_id": exp["issue_id"],
        "issue_number": exp["issue_number"],
    }
    if actual != expected:
        raise DomainError(
            f"trusted identity mismatch: expected {expected}, got {actual}",
            code="DOMAIN_IDENTITY_CONFLICT",
        )
    if manifest["parent"]["commit"] != trusted_binding.parent_sha:
        raise DomainError(
            "trusted parent SHA differs from manifest.parent.commit",
            code="DOMAIN_PARENT_CONFLICT",
        )
    if trusted_binding.host != "github.com":
        raise DomainError("only github.com is supported by the current trusted resolver")

    issue_number = exp["issue_number"]
    experiment_id = f"EXP-{issue_number}"
    root = f"experiments/{experiment_id}"
    binding_path = f"{root}/binding.json"
    manifest_path = f"{root}/manifest.json"
    state_path = f"{root}/state.json"

    for path in (binding_path, manifest_path, state_path):
        if (repo_dir / path).exists():
            raise DomainError(
                f"{experiment_id} already has authoritative domain state at {path}",
                code="EXPERIMENT_IDENTITY_CONFLICT",
            )

    binding = _binding_record(
        request_id=request_id,
        payload_digest=payload_digest,
        manifest=manifest,
    )
    writes = {
        binding_path: binding,
        manifest_path: manifest,
        state_path: _state_record(experiment_id, request_id),
    }
    return DomainPlan(status="APPLIED", experiment_id=experiment_id, writes=writes)
