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
