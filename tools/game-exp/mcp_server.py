from __future__ import annotations

import os
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from client import GameExpClient, GitHubTransport

mcp = MCPServer("game-exp")


def _client(repo: str | None = None) -> GameExpClient:
    target = repo or os.environ.get("GAME_EXP_REPO")
    return GameExpClient(GitHubTransport(target))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def game_exp_status(repo: str | None = None) -> dict[str, Any]:
    """Return the target repository and authoritative game-exp Ledger head."""
    return _client(repo).status()


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def game_exp_doctor(
    repo: str | None = None,
    experiment_id: str | None = None,
) -> dict[str, Any]:
    """Inspect trust prerequisites and optionally one archived experiment's refs."""
    return _client(repo).doctor(experiment_id)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def game_exp_experiment_get(
    experiment_id: str,
    repo: str | None = None,
) -> dict[str, Any]:
    """Return the authoritative experiment projection from the protected Ledger."""
    return _client(repo).experiment_get(experiment_id)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def game_exp_board(
    repo: str | None = None,
) -> dict[str, Any]:
    """Return a lightweight, consistent snapshot of all game-exp experiments."""
    return _client(repo).board()


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True))
def game_exp_experiment_bind(
    manifest: dict[str, Any],
    request_id: str | None = None,
    actor_claim: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Submit a canonical experiment Manifest for trusted GitHub identity binding.

    The protocol binds Manifest operation_id to the request id. If request_id is
    omitted, manifest.operation_id is used as the idempotency key.
    """
    client = _client(repo)
    manifest_request_id = manifest.get("operation_id")
    if not isinstance(manifest_request_id, str) or not manifest_request_id:
        return {
            "status": "REJECTED",
            "repo": client.transport.repo,
            "error": "manifest.operation_id is required",
        }
    if request_id is not None and request_id != manifest_request_id:
        return {
            "status": "REJECTED",
            "repo": client.transport.repo,
            "request_id": request_id,
            "manifest_operation_id": manifest_request_id,
            "error": "request_id must equal manifest.operation_id",
        }
    return client.submit(
        operation="experiment.bind",
        input_value={"manifest": manifest},
        actor_claim=actor_claim,
        request_id=manifest_request_id,
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def game_exp_initialize(
    experiment_id: str,
    repo: str | None = None,
) -> dict[str, Any]:
    """Initialize canonical experiment source refs from the authoritative binding."""
    return _client(repo).initialize(experiment_id)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True))
def game_exp_candidate_build(
    experiment_id: str,
    repo: str | None = None,
) -> dict[str, Any]:
    """Build, attest, retain, and register a new trusted Candidate for an experiment."""
    return _client(repo).candidate(experiment_id)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True))
def game_exp_review_record(
    experiment_id: str,
    outcome: str,
    notes: str,
    candidate_id: str | None = None,
    request_id: str | None = None,
    actor_claim: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Record a human PASS/FAIL Review bound to the current Candidate.

    The MCP caller does not establish reviewer authority. The Trusted Writer
    independently resolves the authenticated GitHub actor and repository
    permission before accepting the Review.
    """
    client = _client(repo)
    if candidate_id is None:
        projection = client.experiment_get(experiment_id)
        if projection.get("status") != "PASS":
            return projection
        candidate_id = projection.get("state", {}).get("current_candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id:
            return {
                "status": "REJECTED",
                "repo": client.transport.repo,
                "experiment_id": experiment_id,
                "error": "experiment has no current Candidate",
            }
    return client.submit(
        operation="review.record",
        input_value={
            "experiment_id": experiment_id,
            "candidate_id": candidate_id,
            "outcome": outcome,
            "notes": notes,
        },
        actor_claim=actor_claim,
        request_id=request_id,
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True))
def game_exp_decision_submit(
    experiment_id: str,
    to_state: str,
    reason: str,
    previous_decision_id: str | None = None,
    request_id: str | None = None,
    actor_claim: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Submit one trusted lifecycle Decision.

    If previous_decision_id is omitted, the tool reads the current protected
    Ledger state and binds the request to its last Decision id. GitHub actor
    identity and permission are still verified by the Trusted Writer.
    """
    client = _client(repo)
    if previous_decision_id is None:
        projection = client.experiment_get(experiment_id)
        if projection.get("status") != "PASS":
            return projection
        previous_decision_id = projection.get("state", {}).get("last_decision_id")
    return client.submit(
        operation="experiment.decision",
        input_value={
            "experiment_id": experiment_id,
            "to_state": to_state,
            "previous_decision_id": previous_decision_id,
            "reason": reason,
        },
        actor_claim=actor_claim,
        request_id=request_id,
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True))
def game_exp_rehearse(
    experiment_id: str,
    repo: str | None = None,
) -> dict[str, Any]:
    """Run a trusted scope-filtered latest-main Rehearsal for the current Candidate."""
    return _client(repo).rehearse(experiment_id)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True))
def game_exp_integrate(
    experiment_id: str,
    repo: str | None = None,
) -> dict[str, Any]:
    """Create or reuse the trusted Integration PR for the current Rehearsal."""
    return _client(repo).integrate(experiment_id)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def game_exp_integrate_finalize(
    experiment_id: str,
    pr_number: str,
    repo: str | None = None,
) -> dict[str, Any]:
    """Verify a merged Integration PR and register the experiment as INTEGRATED."""
    return _client(repo).integrate_finalize(experiment_id, pr_number)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True))
def game_exp_archive(
    experiment_id: str,
    mode: str = "ATOMIC_DELETE",
    repo: str | None = None,
) -> dict[str, Any]:
    """Run the recoverable trusted Archive workflow.

    ATOMIC_DELETE creates the immutable final tag and atomically deletes the
    active experiment branch. RETAIN_BRANCH creates the same official final
    snapshot but retains the branch. The Trusted Archive state machine remains
    authoritative.
    """
    return _client(repo).archive(experiment_id, mode)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True))
def game_exp_archive_abort(
    experiment_id: str,
    archive_id: str,
    reason: str,
    request_id: str | None = None,
    actor_claim: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Abort an Archive only while it is PREPARED and has not been claimed."""
    return _client(repo).archive_abort(
        experiment_id,
        archive_id,
        reason,
        actor_claim=actor_claim,
        request_id=request_id,
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def game_exp_request_get(
    request_id: str,
    repo: str | None = None,
) -> dict[str, Any]:
    """Resolve a request from authoritative Ledger/workflow evidence."""
    return _client(repo).reconcile(request_id)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True))
def game_exp_request_submit(
    operation: str,
    input: dict[str, Any],
    preconditions: dict[str, Any] | None = None,
    actor_claim: str | None = None,
    request_id: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Submit one controlled operation request through the Trusted Writer.

    This tool submits an operation envelope only. It does not claim that the
    requested domain operation has been executed. Use game_exp_request_get to
    resolve ACCEPTED/UNKNOWN requests against the authoritative Ledger.
    """
    return _client(repo).submit(
        operation=operation,
        input_value=input,
        preconditions=preconditions,
        actor_claim=actor_claim,
        request_id=request_id,
    )


if __name__ == "__main__":
    mcp.run()
