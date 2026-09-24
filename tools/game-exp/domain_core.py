from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from protocol_core import ProtocolError, digest_object

SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")
EXP_RE = re.compile(r"^EXP-[1-9][0-9]*$")


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


def plan_domain_mutation(
    *,
    repo_dir: Path,
    payload: dict[str, Any],
    request_id: str,
    payload_digest: str,
    repository_full_name: str,
    trusted_binding: TrustedBindingContext | None = None,
) -> DomainPlan:
    if payload.get("kind") != "operation_request":
        return DomainPlan(status="REQUEST_ONLY", experiment_id=None, writes={})
    operation = payload.get("operation")
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
