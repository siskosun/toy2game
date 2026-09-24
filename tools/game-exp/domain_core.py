from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from protocol_core import ProtocolError, digest_object

SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
EXP_RE = re.compile(r"^EXP-[1-9][0-9]*$")
CANDIDATE_RE = re.compile(r"^C-([1-9][0-9]*)-([1-9][0-9]*)-([1-9][0-9]*)$")


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
class TrustedCandidateContext:
    experiment_id: str
    candidate_id: str
    source_anchor_ref: str
    source_sha: str
    artifact_digest: str
    manifest_digest: str
    workflow_source_sha: str
    run_id: str
    run_attempt: str
    policy_digest: str
    dependency_lock_digest: str
    environment_digest: str
    release_tag: str


@dataclass(frozen=True)
class DomainPlan:
    status: str
    experiment_id: str | None
    writes: dict[str, dict[str, Any]]

    @property
    def paths(self) -> list[str]:
        return sorted(self.writes)


CANDIDATE_REQUIRED_CHECKS = (
    "project-tests",
    "project-build",
    "artifact-observe",
    "attestation-verify",
    "immutable-retention",
)


def candidate_policy() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "artifact_level": 3,
        "required_checks": list(CANDIDATE_REQUIRED_CHECKS),
        "retention_provider": "github-immutable-release",
        "source_anchor": "protected-annotated-tag",
    }


def candidate_policy_digest() -> str:
    return digest_object(candidate_policy())


def _validate_candidate_checks(checks: Any) -> list[dict[str, Any]]:
    if not isinstance(checks, list) or not checks:
        raise DomainError("candidate checks must be a non-empty list")
    if len(checks) != len(CANDIDATE_REQUIRED_CHECKS):
        raise DomainError("candidate checks do not match trusted policy")
    normalized: list[dict[str, Any]] = []
    for index, expected_name in enumerate(CANDIDATE_REQUIRED_CHECKS):
        row = checks[index]
        if not isinstance(row, dict):
            raise DomainError("candidate check must be an object")
        _expect_keys(row, {"name", "provenance", "status"}, where="candidate.check")
        if row["name"] != expected_name:
            raise DomainError(
                f"candidate check order/name mismatch at {index}: expected {expected_name}"
            )
        if row["provenance"] != "TRUSTED_OBSERVED" or row["status"] != "PASS":
            raise DomainError(f"candidate check {expected_name} is not trusted PASS")
        normalized.append(dict(row))
    return normalized


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


def _load_current_candidate(repo_dir: Path, experiment_id: str) -> dict[str, Any]:
    pointer_path = f"experiments/{experiment_id}/current_candidate.json"
    pointer = _read_json_file(
        repo_dir,
        pointer_path,
        where=f"{experiment_id} current candidate",
    )
    if pointer.get("kind") != "candidate_pointer" or pointer.get("experiment_id") != experiment_id:
        raise DomainError("current candidate pointer identity mismatch", code="DOMAIN_PREREQUISITE_MISSING")
    candidate_id = pointer.get("candidate_id")
    if not isinstance(candidate_id, str):
        raise DomainError("current candidate id missing", code="DOMAIN_PREREQUISITE_MISSING")
    record = _read_json_file(
        repo_dir,
        f"experiments/{experiment_id}/candidates/{candidate_id}.json",
        where=f"{experiment_id} current candidate",
    )
    if pointer.get("candidate_digest") != digest_object(record):
        raise DomainError("current candidate pointer digest mismatch", code="DOMAIN_PREREQUISITE_MISSING")
    if (
        record.get("kind") != "candidate"
        or record.get("experiment_id") != experiment_id
        or record.get("candidate_id") != candidate_id
    ):
        raise DomainError("current candidate record identity mismatch", code="DOMAIN_PREREQUISITE_MISSING")
    if record.get("artifact_level") != 3:
        raise DomainError("current candidate is not Level 3", code="DOMAIN_PREREQUISITE_MISSING")
    if record.get("policy_digest") != candidate_policy_digest():
        raise DomainError("current candidate policy is stale", code="DOMAIN_PREREQUISITE_MISSING")
    _validate_candidate_checks(record.get("checks"))
    return record


def _plan_candidate_attest(
    *,
    repo_dir: Path,
    payload: dict[str, Any],
    request_id: str,
    trusted_candidate: TrustedCandidateContext | None,
) -> DomainPlan:
    input_value = _mapping(payload.get("input"), "operation.input")
    _expect_keys(
        input_value,
        {
            "experiment_id",
            "candidate_id",
            "source_sha",
            "source_anchor_ref",
            "manifest_digest",
            "artifact_digest",
            "artifact_level",
            "workflow_source_sha",
            "run_id",
            "run_attempt",
            "policy_digest",
            "dependency_lock_digest",
            "environment_digest",
            "checks",
            "retention",
        },
        where="operation.input",
    )
    experiment_id = _string(input_value["experiment_id"], "operation.input.experiment_id")
    candidate_id = _string(input_value["candidate_id"], "operation.input.candidate_id")
    match = CANDIDATE_RE.fullmatch(candidate_id)
    if not match or experiment_id != f"EXP-{match.group(1)}":
        raise DomainError("candidate_id does not match experiment identity")
    source_sha = _string(input_value["source_sha"], "operation.input.source_sha")
    workflow_source_sha = _string(
        input_value["workflow_source_sha"],
        "operation.input.workflow_source_sha",
    )
    if not SHA_RE.fullmatch(source_sha) or not SHA_RE.fullmatch(workflow_source_sha):
        raise DomainError("candidate source/workflow SHA is invalid")
    source_anchor_ref = _string(
        input_value["source_anchor_ref"],
        "operation.input.source_anchor_ref",
    )
    expected_anchor = (
        f"refs/tags/exp-candidate/{match.group(1)}/{candidate_id}"
    )
    if source_anchor_ref != expected_anchor:
        raise DomainError("candidate source anchor ref is not canonical")

    manifest_digest = _string(
        input_value["manifest_digest"],
        "operation.input.manifest_digest",
    )
    artifact_digest = _string(
        input_value["artifact_digest"],
        "operation.input.artifact_digest",
    )
    policy_digest = _string(
        input_value["policy_digest"],
        "operation.input.policy_digest",
    )
    dependency_lock_digest = _string(
        input_value["dependency_lock_digest"],
        "operation.input.dependency_lock_digest",
    )
    environment_digest = _string(
        input_value["environment_digest"],
        "operation.input.environment_digest",
    )
    for name, value in (
        ("manifest_digest", manifest_digest),
        ("artifact_digest", artifact_digest),
        ("policy_digest", policy_digest),
        ("dependency_lock_digest", dependency_lock_digest),
        ("environment_digest", environment_digest),
    ):
        if not DIGEST_RE.fullmatch(value):
            raise DomainError(f"candidate {name} is invalid")
    if input_value["artifact_level"] != 3:
        raise DomainError("candidate artifact_level must equal 3")
    if policy_digest != candidate_policy_digest():
        raise DomainError("candidate policy digest does not match trusted policy")

    run_id = _canonical_decimal(input_value["run_id"], "operation.input.run_id")
    run_attempt = _canonical_decimal(
        input_value["run_attempt"],
        "operation.input.run_attempt",
    )
    if match.group(2) != run_id or match.group(3) != run_attempt:
        raise DomainError("candidate_id run identity mismatch")

    checks = _validate_candidate_checks(input_value["checks"])
    retention = _mapping(input_value["retention"], "operation.input.retention")
    _expect_keys(
        retention,
        {"provider", "release_tag", "asset_name"},
        where="operation.input.retention",
    )
    if retention["provider"] != "github-immutable-release":
        raise DomainError("candidate retention provider is not trusted")
    if retention["asset_name"] != "candidate.tgz":
        raise DomainError("candidate retention asset must be candidate.tgz")
    release_tag = _string(
        retention["release_tag"],
        "operation.input.retention.release_tag",
    )
    if release_tag != f"game-exp-candidate-{run_id}-{run_attempt}":
        raise DomainError("candidate release tag is not canonical")

    binding, manifest, state, _operation = _load_bound_experiment(
        repo_dir,
        experiment_id,
    )
    if state.get("archive_lock") is not None:
        raise DomainError("candidate rejected while archive lock is active", code="DOMAIN_CANDIDATE_CONFLICT")
    if state["lifecycle"] not in {"REVIEW", "PROMISING"}:
        raise DomainError(
            f"candidate attestation requires REVIEW or PROMISING, got {state['lifecycle']}",
            code="DOMAIN_INVALID_TRANSITION",
        )
    if manifest_digest != binding["initialization"]["manifest_digest"]:
        raise DomainError("candidate manifest digest differs from binding")

    if trusted_candidate is None:
        raise DomainError("trusted candidate context is required")
    expected_context = TrustedCandidateContext(
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
    if trusted_candidate != expected_context:
        raise DomainError(
            "candidate evidence differs from independently verified GitHub facts",
            code="DOMAIN_CANDIDATE_CONFLICT",
        )

    candidate_path = f"experiments/{experiment_id}/candidates/{candidate_id}.json"
    if (repo_dir / candidate_path).exists():
        raise DomainError("candidate_id already exists", code="DOMAIN_CANDIDATE_CONFLICT")

    record = {
        "kind": "candidate",
        "candidate_id": candidate_id,
        "experiment_id": experiment_id,
        "request_id": request_id,
        "source_sha": source_sha,
        "source_anchor_ref": source_anchor_ref,
        "manifest_digest": manifest_digest,
        "artifact_digest": artifact_digest,
        "artifact_level": 3,
        "workflow_source_sha": workflow_source_sha,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "policy_digest": policy_digest,
        "dependency_lock_digest": dependency_lock_digest,
        "environment_digest": environment_digest,
        "runtime": dict(manifest["runtime"]),
        "checks": checks,
        "retention": dict(retention),
    }
    actor_claim = payload.get("actor_claim")
    if actor_claim is not None:
        record["actor_claim"] = actor_claim
    pointer = {
        "kind": "candidate_pointer",
        "experiment_id": experiment_id,
        "candidate_id": candidate_id,
        "candidate_digest": digest_object(record),
        "updated_by_request_id": request_id,
    }
    return DomainPlan(
        status="APPLIED",
        experiment_id=experiment_id,
        writes={
            candidate_path: record,
            f"experiments/{experiment_id}/current_candidate.json": pointer,
        },
    )


def _plan_decision(
    *,
    repo_dir: Path,
    payload: dict[str, Any],
    request_id: str,
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
    if to_state in {"PROMISING", "SELECTED"}:
        _load_current_candidate(repo_dir, experiment_id)

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
    }
    actor_claim = payload.get("actor_claim")
    if actor_claim is not None:
        decision["actor_claim"] = actor_claim

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


def plan_domain_mutation(
    *,
    repo_dir: Path,
    payload: dict[str, Any],
    request_id: str,
    payload_digest: str,
    repository_full_name: str,
    trusted_binding: TrustedBindingContext | None = None,
    trusted_candidate: TrustedCandidateContext | None = None,
) -> DomainPlan:
    if payload.get("kind") != "operation_request":
        return DomainPlan(status="REQUEST_ONLY", experiment_id=None, writes={})
    operation = payload.get("operation")
    if operation == "experiment.decision":
        return _plan_decision(
            repo_dir=repo_dir,
            payload=payload,
            request_id=request_id,
        )
    if operation == "candidate.attest":
        return _plan_candidate_attest(
            repo_dir=repo_dir,
            payload=payload,
            request_id=request_id,
            trusted_candidate=trusted_candidate,
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
