from __future__ import annotations

import os
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from client import GameExpClient, GitHubTransport
from project_setup import preflight as project_preflight, provision as project_provision
from conformance_core import (
    ConformanceClient,
    compare_reports as conformance_compare_reports,
    evaluate as conformance_evaluate,
    load_session as conformance_load_session,
    save_session as conformance_save_session,
    start_session as conformance_start_session,
    suite_descriptor as conformance_suite_descriptor,
)

mcp = MCPServer("game-exp")


def _conformance_session_path() -> str | None:
    value = os.environ.get("GAME_EXP_CONFORMANCE_SESSION")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _client(repo: str | None = None) -> GameExpClient | ConformanceClient:
    session_path = _conformance_session_path()
    if session_path is not None:
        return ConformanceClient(session_path, surface="mcp")
    target = repo or os.environ.get("GAME_EXP_REPO")
    return GameExpClient(GitHubTransport(target))


def _mcp_transport() -> str:
    return os.environ.get("GAME_EXP_MCP_TRANSPORT", "stdio").strip().lower() or "stdio"


def _http_single_principal_write_enabled() -> bool:
    return os.environ.get(
        "GAME_EXP_MCP_TRUSTED_SINGLE_PRINCIPAL",
        "",
    ).strip().lower() in {"1", "true", "yes"}


def _write_identity_rejection(repo: str | None) -> dict[str, Any] | None:
    if _conformance_session_path() is not None:
        return None
    if _mcp_transport() != "streamable-http":
        return None
    if _http_single_principal_write_enabled():
        return None
    client = _client(repo)
    return {
        "status": "REJECTED",
        "code": "MCP_HTTP_WRITE_IDENTITY_UNBOUND",
        "repo": client.transport.repo,
        "error": (
            "Streamable HTTP write identity is not bound per caller. "
            "Use a per-user local stdio/CLI identity, GitHub Bridge, or explicitly "
            "configure a trusted single-principal HTTP endpoint."
        ),
        "fallback_policy": "AUTHORIZATION_FAILURE_DO_NOT_RETRY_AS_NEW_OPERATION",
    }


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
def game_exp_conformance_suite() -> dict[str, Any]:
    """Return the fixed synthetic behavior screening suite. Never touches GitHub."""
    result = conformance_suite_descriptor()
    return {"status": "PASS", "conformance_simulation": True, **result}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False))
def game_exp_conformance_start(
    scenario_id: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Reset the configured synthetic session file to one conformance scenario."""
    path = _conformance_session_path()
    if path is None:
        return {
            "status": "REJECTED",
            "code": "CONFORMANCE_SESSION_PATH_REQUIRED",
            "error": "set GAME_EXP_CONFORMANCE_SESSION before starting simulator mode",
        }
    session = conformance_start_session(scenario_id, session_id=session_id)
    conformance_save_session(path, session)
    return {
        "status": "PASS",
        "conformance_simulation": True,
        "session_id": session["session_id"],
        "scenario_id": session["scenario_id"],
        "suite_digest": session["suite_digest"],
        "task_zh": session["task_zh"],
    }


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
def game_exp_conformance_compare(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Compare two screening reports only when their suite digest is identical."""
    return conformance_compare_reports(baseline, candidate)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False))
def game_exp_conformance_result() -> dict[str, Any]:
    """Evaluate the configured synthetic session against the standing suite."""
    path = _conformance_session_path()
    if path is None:
        return {
            "status": "REJECTED",
            "code": "CONFORMANCE_SESSION_PATH_REQUIRED",
            "error": "set GAME_EXP_CONFORMANCE_SESSION before evaluating simulator mode",
        }
    session = conformance_load_session(path)
    if session.get("status") == "EVALUATED" and isinstance(
        session.get("evaluation"), dict
    ):
        return session["evaluation"]
    result = conformance_evaluate(session)
    conformance_save_session(path, session)
    return result


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def game_exp_project_preflight(repo: str | None = None) -> dict[str, Any]:
    """Check whether a repository can support a complete trusted game-exp setup."""
    if _conformance_session_path() is not None:
        return {
            "status": "REJECTED",
            "complete": False,
            "code": "PROJECT_SETUP_NOT_AVAILABLE_IN_CONFORMANCE",
        }
    target = GitHubTransport(repo or os.environ.get("GAME_EXP_REPO")).repo
    return project_preflight(target)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def game_exp_project_init(
    repo: str | None = None,
    run_selftest: bool = True,
) -> dict[str, Any]:
    """Provision all repository trust controls. Only local stdio MCP may run this."""
    if _conformance_session_path() is not None:
        return {
            "status": "REJECTED",
            "complete": False,
            "code": "PROJECT_SETUP_NOT_AVAILABLE_IN_CONFORMANCE",
        }
    if _mcp_transport() != "stdio":
        target = GitHubTransport(repo or os.environ.get("GAME_EXP_REPO")).repo
        return {
            "status": "REJECTED",
            "complete": False,
            "repo": target,
            "code": "PROJECT_SETUP_LOCAL_STDIO_REQUIRED",
            "error": (
                "project-init generates a repository Deploy Key and writes an Actions "
                "secret; run it through local CLI/stdio MCP with the repository admin "
                "GitHub principal, never through shared HTTP MCP"
            ),
        }
    target = GitHubTransport(repo or os.environ.get("GAME_EXP_REPO")).repo
    result = project_provision(target, run_selftest=run_selftest)
    if not run_selftest and result.get("status") == "PASS":
        result["status"] = "INCOMPLETE"
        result["complete"] = False
        result["reason"] = "Trusted Writer self-test was skipped"
    return result


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def game_exp_status(repo: str | None = None) -> dict[str, Any]:
    """Return the target repository and authoritative game-exp Ledger head."""
    return _client(repo).status()

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def game_exp_access_check(repo: str | None = None) -> dict[str, Any]:
    """Return the current GitHub repository access level for onboarding and gating."""
    return _client(repo).access_check()


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def game_exp_capabilities(repo: str | None = None) -> dict[str, Any]:
    """Return versioned business capabilities and the active MCP identity boundary."""
    result = _client(repo).capabilities()
    transport = _mcp_transport()
    result["interface"] = {
        "type": "mcp",
        "transport": transport,
        "write_identity": (
            "synthetic-conformance"
            if _conformance_session_path() is not None
            else "local-gh-principal"
            if transport == "stdio"
            else "trusted-single-principal"
            if _http_single_principal_write_enabled()
            else "unbound-read-only"
        ),
        "shared_http_writes_allowed": (
            transport != "streamable-http"
            or _http_single_principal_write_enabled()
        ),
    }
    return result



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
    query: str | None = None,
    subject_id: str | None = None,
    lifecycle: str | None = None,
    attention_only: bool = False,
) -> dict[str, Any]:
    """Return a Chinese-ready Board with project readiness, repository/access/trust
    context, experiment statistics, onboarding/next-action guidance, and an optional
    read-only focus set from one pinned Ledger snapshot."""
    return _client(repo).board(
        query=query,
        subject_id=subject_id,
        lifecycle=lifecycle,
        attention_only=attention_only,
    )

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def game_exp_experiment_panel(
    experiment_id: str,
    repo: str | None = None,
) -> dict[str, Any]:
    """Return one Chinese-ready single-experiment panel from one pinned Ledger snapshot."""
    return _client(repo).experiment_panel(experiment_id)

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def game_exp_subject_panel(
    subject_id: str,
    repo: str | None = None,
) -> dict[str, Any]:
    """Return one Chinese-ready subject/prototype panel from one pinned Ledger snapshot."""
    return _client(repo).subject_panel(subject_id)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def game_exp_prototype_handoff(
    experiment_id: str,
    repo: str | None = None,
) -> dict[str, Any]:
    """Return the exact implementation brief for handing one experiment to Godot Prototype Studio."""
    return _client(repo).prototype_handoff(experiment_id)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def game_exp_notifications(
    repo: str | None = None,
    viewer_login: str | None = None,
    subject_id: str | None = None,
    limit: int = 50,
    after: str | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Return cursor-resumable collaboration events from committed Ledger snapshots."""
    return _client(repo).notification_feed(
        viewer_login=viewer_login,
        subject_id=subject_id,
        limit=limit,
        after=after,
        cursor=cursor,
    )


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
    blocked = _write_identity_rejection(repo)
    if blocked is not None:
        return blocked
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
    request_id: str,
    actor_claim: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Initialize canonical source refs under a stable cross-interface operation id."""
    blocked = _write_identity_rejection(repo)
    if blocked is not None:
        return blocked
    return _client(repo).initialize(
        experiment_id,
        request_id=request_id,
        actor_claim=actor_claim,
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def game_exp_candidate_build(
    experiment_id: str,
    request_id: str,
    actor_claim: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Build/register a trusted Candidate under a stable cross-interface operation id."""
    blocked = _write_identity_rejection(repo)
    if blocked is not None:
        return blocked
    return _client(repo).candidate(
        experiment_id,
        request_id=request_id,
        actor_claim=actor_claim,
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def game_exp_review_record(
    experiment_id: str,
    outcome: str,
    notes: str,
    request_id: str,
    candidate_id: str | None = None,
    actor_claim: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Record human PASS/FAIL for one concrete Candidate using a stable request id."""
    blocked = _write_identity_rejection(repo)
    if blocked is not None:
        return blocked
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
                "request_id": request_id,
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


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def game_exp_decision_submit(
    experiment_id: str,
    to_state: str,
    reason: str,
    request_id: str,
    previous_decision_id: str | None = None,
    actor_claim: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Submit one lifecycle Decision bound to protected state and a stable request id."""
    blocked = _write_identity_rejection(repo)
    if blocked is not None:
        return blocked
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


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def game_exp_rehearse(
    experiment_id: str,
    request_id: str,
    actor_claim: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Run latest-main trusted Rehearsal under a stable cross-interface operation id."""
    blocked = _write_identity_rejection(repo)
    if blocked is not None:
        return blocked
    return _client(repo).rehearse(
        experiment_id,
        request_id=request_id,
        actor_claim=actor_claim,
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def game_exp_integrate(
    experiment_id: str,
    request_id: str,
    actor_claim: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Create/reuse the Integration PR under a stable cross-interface operation id."""
    blocked = _write_identity_rejection(repo)
    if blocked is not None:
        return blocked
    return _client(repo).integrate(
        experiment_id,
        request_id=request_id,
        actor_claim=actor_claim,
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def game_exp_integrate_finalize(
    experiment_id: str,
    pr_number: str,
    request_id: str,
    actor_claim: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Finalize an actually merged Integration PR under the same operation contract."""
    blocked = _write_identity_rejection(repo)
    if blocked is not None:
        return blocked
    return _client(repo).integrate_finalize(
        experiment_id,
        pr_number,
        request_id=request_id,
        actor_claim=actor_claim,
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True))
def game_exp_archive(
    experiment_id: str,
    request_id: str,
    mode: str = "ATOMIC_DELETE",
    actor_claim: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Run recoverable Archive under a stable cross-interface operation id."""
    blocked = _write_identity_rejection(repo)
    if blocked is not None:
        return blocked
    return _client(repo).archive(
        experiment_id,
        mode,
        request_id=request_id,
        actor_claim=actor_claim,
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True))
def game_exp_archive_abort(
    experiment_id: str,
    archive_id: str,
    reason: str,
    request_id: str,
    actor_claim: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Abort an Archive only while it is PREPARED and has not been claimed."""
    blocked = _write_identity_rejection(repo)
    if blocked is not None:
        return blocked
    return _client(repo).archive_abort(
        experiment_id,
        archive_id,
        reason,
        actor_claim=actor_claim,
        request_id=request_id,
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def game_exp_operation_get(
    request_id: str,
    repo: str | None = None,
) -> dict[str, Any]:
    """Resolve a trusted request or async execution using the exact same operation id."""
    return _client(repo).operation_get(request_id)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def game_exp_request_get(
    request_id: str,
    repo: str | None = None,
) -> dict[str, Any]:
    """Backward-compatible alias for game_exp_operation_get."""
    return _client(repo).operation_get(request_id)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def game_exp_operation_resume(
    request_id: str,
    repo: str | None = None,
) -> dict[str, Any]:
    """Resume only the already-claimed async operation with this exact operation id."""
    blocked = _write_identity_rejection(repo)
    if blocked is not None:
        return blocked
    return _client(repo).resume_execution(request_id)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def game_exp_request_submit(
    operation: str,
    input: dict[str, Any],
    request_id: str,
    preconditions: dict[str, Any] | None = None,
    actor_claim: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Submit one controlled operation request through the Trusted Writer.

    This tool submits an operation envelope only. It does not claim that the
    requested domain operation has been executed. Use game_exp_request_get to
    resolve ACCEPTED/UNKNOWN requests against the authoritative Ledger. A stable
    request_id is mandatory; authorization failures are not a signal to retry
    through another interface.
    """
    blocked = _write_identity_rejection(repo)
    if blocked is not None:
        return blocked
    return _client(repo).submit(
        operation=operation,
        input_value=input,
        preconditions=preconditions,
        actor_claim=actor_claim,
        request_id=request_id,
    )


def _http_port() -> int:
    raw = os.environ.get("GAME_EXP_MCP_PORT", "8765").strip()
    try:
        port = int(raw)
    except ValueError as exc:
        raise RuntimeError("GAME_EXP_MCP_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError("GAME_EXP_MCP_PORT must be between 1 and 65535")
    return port


def _run_server() -> None:
    transport = os.environ.get("GAME_EXP_MCP_TRANSPORT", "stdio").strip().lower()
    if transport == "stdio":
        mcp.run()
        return
    if transport != "streamable-http":
        raise RuntimeError(
            "GAME_EXP_MCP_TRANSPORT must be stdio or streamable-http"
        )

    path = os.environ.get("GAME_EXP_MCP_PATH", "/mcp").strip() or "/mcp"
    if not path.startswith("/"):
        raise RuntimeError("GAME_EXP_MCP_PATH must start with /")

    mcp.run(
        transport="streamable-http",
        host=os.environ.get("GAME_EXP_MCP_HOST", "127.0.0.1").strip()
        or "127.0.0.1",
        port=_http_port(),
        streamable_http_path=path,
        stateless_http=True,
        json_response=True,
    )


if __name__ == "__main__":
    _run_server()
