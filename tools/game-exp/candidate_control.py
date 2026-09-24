from __future__ import annotations

import argparse
import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from domain_core import CANDIDATE_REQUIRED_CHECKS, candidate_policy_digest
from protocol_core import build_operation_payload, canonical_json_bytes
from source_initializer import InitError, load_authoritative_binding

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
EXP_RE = re.compile(r"^EXP-([1-9][0-9]*)$")


class CandidateControlError(RuntimeError):
    pass


def github_json(repo: str, suffix: str) -> dict[str, Any]:
    token = os.environ.get("GAME_EXP_GITHUB_TOKEN")
    if not token:
        raise CandidateControlError("GAME_EXP_GITHUB_TOKEN is required")
    req = urllib.request.Request(
        "https://api.github.com/repos/" + repo + suffix,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "game-exp-candidate-control",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            value = json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[-1000:]
        raise CandidateControlError(
            f"GitHub API failed ({exc.code}) for {suffix}: {body}"
        ) from exc
    except Exception as exc:
        raise CandidateControlError(f"GitHub API failed for {suffix}: {exc}") from exc
    if not isinstance(value, dict):
        raise CandidateControlError(f"GitHub API returned non-object for {suffix}")
    return value


def _tag_metadata(message: Any) -> dict[str, str]:
    if not isinstance(message, str):
        raise CandidateControlError("base tag message missing")
    out: dict[str, str] = {}
    for line in message.splitlines():
        if ": " in line:
            key, value = line.split(": ", 1)
            out[key.strip()] = value.strip()
    return out


def prepare_candidate(
    *,
    repo: str,
    experiment_id: str,
    run_id: str,
    run_attempt: str,
    workflow_source_sha: str,
) -> dict[str, Any]:
    match = EXP_RE.fullmatch(experiment_id)
    if not match:
        raise CandidateControlError("experiment_id must be EXP-<positive integer>")
    if not run_id.isdigit() or not run_attempt.isdigit():
        raise CandidateControlError("run_id/run_attempt must be decimal strings")
    if not SHA_RE.fullmatch(workflow_source_sha):
        raise CandidateControlError("workflow_source_sha must be a commit SHA")

    binding, _manifest, state, _operation = load_authoritative_binding(repo, experiment_id)
    if state.get("archive_lock") is not None:
        raise CandidateControlError("candidate build blocked by archive lock")
    if state.get("lifecycle") not in {"REVIEW", "PROMISING"}:
        raise CandidateControlError(
            f"candidate build requires REVIEW or PROMISING, got {state.get('lifecycle')}"
        )

    issue = match.group(1)
    branch_ref = f"refs/heads/exp/{issue}"
    branch_data = github_json(
        repo,
        f"/git/ref/heads/exp/{urllib.parse.quote(issue, safe='')}",
    )
    branch_object = branch_data.get("object") or {}
    source_sha = branch_object.get("sha")
    if branch_object.get("type") != "commit" or not isinstance(source_sha, str):
        raise CandidateControlError("experiment source branch does not resolve to a commit")
    if not SHA_RE.fullmatch(source_sha):
        raise CandidateControlError("experiment source SHA is invalid")

    tag_ref = f"refs/tags/exp-base/{issue}"
    tag_ref_data = github_json(
        repo,
        f"/git/ref/tags/exp-base/{urllib.parse.quote(issue, safe='')}",
    )
    tag_ref_object = tag_ref_data.get("object") or {}
    if tag_ref_object.get("type") != "tag" or not isinstance(tag_ref_object.get("sha"), str):
        raise CandidateControlError("base tag must be annotated")
    tag_data = github_json(
        repo,
        f"/git/tags/{urllib.parse.quote(tag_ref_object['sha'], safe='')}",
    )
    tag_object = tag_data.get("object") or {}
    if tag_object.get("type") != "commit" or tag_object.get("sha") != binding.get("parent_sha"):
        raise CandidateControlError("base tag target differs from frozen parent")
    metadata = _tag_metadata(tag_data.get("message"))
    init_sha = metadata.get("game-exp-initialization-commit")
    plan_digest = metadata.get("game-exp-initialization-plan")
    if not isinstance(init_sha, str) or not SHA_RE.fullmatch(init_sha):
        raise CandidateControlError("base tag initialization commit missing")
    if plan_digest != binding["initialization"]["initialization_plan_digest"]:
        raise CandidateControlError("base tag initialization plan digest mismatch")

    compare = github_json(
        repo,
        f"/compare/{urllib.parse.quote(init_sha, safe='')}...{urllib.parse.quote(source_sha, safe='')}",
    )
    if compare.get("status") not in {"ahead", "identical"}:
        raise CandidateControlError(
            f"experiment source is not descended from initialization commit: {compare.get('status')}"
        )

    candidate_id = f"C-{issue}-{run_id}-{run_attempt}"
    release_tag = f"game-exp-candidate-{run_id}-{run_attempt}"
    source_anchor_ref = f"refs/tags/exp-candidate/{issue}/{candidate_id}"
    return {
        "status": "READY",
        "experiment_id": experiment_id,
        "issue_number": issue,
        "source_branch_ref": branch_ref,
        "source_sha": source_sha,
        "base_tag_ref": tag_ref,
        "initialization_commit": init_sha,
        "manifest_digest": binding["initialization"]["manifest_digest"],
        "candidate_id": candidate_id,
        "source_anchor_ref": source_anchor_ref,
        "release_tag": release_tag,
        "policy_digest": candidate_policy_digest(),
        "workflow_source_sha": workflow_source_sha,
        "run_id": run_id,
        "run_attempt": run_attempt,
    }


def candidate_payload(
    *,
    experiment_id: str,
    candidate_id: str,
    source_sha: str,
    source_anchor_ref: str,
    manifest_digest: str,
    artifact_digest: str,
    workflow_source_sha: str,
    run_id: str,
    run_attempt: str,
    policy_digest: str,
    release_tag: str,
) -> dict[str, Any]:
    checks = [
        {"name": name, "provenance": "TRUSTED_OBSERVED", "status": "PASS"}
        for name in CANDIDATE_REQUIRED_CHECKS
    ]
    return build_operation_payload(
        "candidate.attest",
        {
            "experiment_id": experiment_id,
            "candidate_id": candidate_id,
            "source_sha": source_sha,
            "source_anchor_ref": source_anchor_ref,
            "manifest_digest": manifest_digest,
            "artifact_digest": artifact_digest,
            "artifact_level": 3,
            "workflow_source_sha": workflow_source_sha,
            "run_id": run_id,
            "run_attempt": run_attempt,
            "policy_digest": policy_digest,
            "checks": checks,
            "retention": {
                "provider": "github-immutable-release",
                "release_tag": release_tag,
                "asset_name": "candidate.tgz",
            },
        },
        actor_claim="trusted-candidate-workflow",
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command", required=True)

    prep = sub.add_parser("prepare")
    prep.add_argument("--repo", required=True)
    prep.add_argument("--experiment-id", required=True)
    prep.add_argument("--run-id", required=True)
    prep.add_argument("--run-attempt", required=True)
    prep.add_argument("--workflow-source-sha", required=True)

    payload = sub.add_parser("payload")
    for name in (
        "experiment-id",
        "candidate-id",
        "source-sha",
        "source-anchor-ref",
        "manifest-digest",
        "artifact-digest",
        "workflow-source-sha",
        "run-id",
        "run-attempt",
        "policy-digest",
        "release-tag",
    ):
        payload.add_argument("--" + name, required=True)

    args = ap.parse_args()
    if args.command == "prepare":
        result = prepare_candidate(
            repo=args.repo,
            experiment_id=args.experiment_id,
            run_id=args.run_id,
            run_attempt=args.run_attempt,
            workflow_source_sha=args.workflow_source_sha,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return 0

    value = candidate_payload(
        experiment_id=args.experiment_id,
        candidate_id=args.candidate_id,
        source_sha=args.source_sha,
        source_anchor_ref=args.source_anchor_ref,
        manifest_digest=args.manifest_digest,
        artifact_digest=args.artifact_digest,
        workflow_source_sha=args.workflow_source_sha,
        run_id=args.run_id,
        run_attempt=args.run_attempt,
        policy_digest=args.policy_digest,
        release_tag=args.release_tag,
    )
    raw = canonical_json_bytes(value)
    print(
        json.dumps(
            {
                "payload": value,
                "payload_b64": base64.b64encode(raw).decode("ascii"),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (InitError, CandidateControlError) as exc:
        print(
            json.dumps(
                {"status": "CANDIDATE_CONTROL_INVALID", "error": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        raise SystemExit(47)
