from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import archive_control


class ArchiveRunnerError(RuntimeError):
    pass


def run(command, *, cwd=None, env=None, check=True):
    proc = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and proc.returncode != 0:
        raise ArchiveRunnerError(
            f"command failed ({proc.returncode}): {' '.join(command)}\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return proc


def ledger_head(repo: str) -> str:
    value = archive_control.github_json(repo, "/git/ref/heads/game-exp/ledger")
    sha = ((value or {}).get("object") or {}).get("sha")
    if not isinstance(sha, str) or len(sha) != 40:
        raise ArchiveRunnerError("authoritative Ledger head is unavailable")
    return sha


def status(repo: str, experiment_id: str, mode: str) -> dict[str, Any]:
    return archive_control.status(
        SimpleNamespace(repo=repo, experiment_id=experiment_id, mode=mode)
    )


def payload_info(
    *,
    operation: str,
    experiment_id: str,
    mode: str,
    archive_id: str | None,
    run_id: str,
    run_attempt: str,
    actor_claim: str,
) -> dict[str, Any]:
    return archive_control.request_payload(
        SimpleNamespace(
            operation=operation,
            experiment_id=experiment_id,
            archive_id=archive_id,
            mode=mode,
            run_id=run_id,
            run_attempt=run_attempt,
            actor_claim=actor_claim,
        )
    )


def submit_writer(
    *,
    repo: str,
    info: dict[str, Any],
    authority: str,
    ssh_key: str,
    run_id: str,
    run_attempt: str,
    workflow_source_sha: str,
    writer_path: str,
) -> dict[str, Any]:
    last = None
    for _ in range(3):
        head = ledger_head(repo)
        proc = run(
            [
                sys.executable,
                writer_path,
                "--repo",
                repo,
                "--request-id",
                info["request_id"],
                "--expected-head",
                head,
                "--payload-b64",
                info["payload_b64"],
                "--ssh-key",
                ssh_key,
                "--run-id",
                run_id,
                "--run-attempt",
                run_attempt,
                "--workflow-source-sha",
                workflow_source_sha,
                "--authority",
                authority,
            ],
            check=False,
        )
        last = proc
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        result = None
        if lines:
            try:
                result = json.loads(lines[-1])
            except json.JSONDecodeError:
                result = None
        if proc.returncode == 0:
            return result or {"status": "COMMITTED"}
        if proc.returncode == 42:
            continue
        raise ArchiveRunnerError(
            f"Trusted Writer failed ({proc.returncode}) for {info['request_id']}\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    raise ArchiveRunnerError(
        "Trusted Writer CAS did not converge after 3 attempts"
        + (f"\nstdout:\n{last.stdout}\nstderr:\n{last.stderr}" if last else "")
    )


def remote_state(repo: str, experiment_id: str, archive_id: str) -> dict[str, Any]:
    return archive_control.remote_state(
        SimpleNamespace(
            repo=repo,
            experiment_id=experiment_id,
            archive_id=archive_id,
        )
    )


def mutate_refs(
    *,
    repo: str,
    archive: dict[str, Any],
    ssh_key: str,
) -> dict[str, Any]:
    archive_id = archive["archive_id"]
    experiment_id = archive["experiment_id"]
    mode = archive["mode"]
    expected = archive["expected_branch_sha"]
    branch_ref = archive["branch_ref"]
    final_tag_ref = archive["final_tag_ref"]
    before = remote_state(repo, experiment_id, archive_id)
    if before["classification"] != "NOT_EXECUTED":
        return {
            "attempted": False,
            "push_returncode": None,
            "before": before,
            "after": before,
        }

    env = os.environ.copy()
    env["GIT_SSH_COMMAND"] = (
        f'ssh -i "{ssh_key}" -o IdentitiesOnly=yes '
        "-o StrictHostKeyChecking=accept-new"
    )
    with tempfile.TemporaryDirectory(prefix="game-exp-archive-ref-") as td:
        work = Path(td)
        run(["git", "init", str(work)])
        run(["git", "config", "user.name", "game-exp-archive-writer"], cwd=work)
        run(
            [
                "git",
                "config",
                "user.email",
                "game-exp-archive-writer@users.noreply.github.com",
            ],
            cwd=work,
        )
        run(
            ["git", "remote", "add", "origin", f"git@github.com:{repo}.git"],
            cwd=work,
        )
        run(["git", "fetch", "--no-tags", "origin", branch_ref], cwd=work, env=env)
        fetched = run(["git", "rev-parse", "FETCH_HEAD"], cwd=work).stdout.strip()
        if fetched != expected:
            raise ArchiveRunnerError(
                f"archive branch moved before ref mutation: expected={expected} actual={fetched}"
            )

        tag_name = final_tag_ref.removeprefix("refs/tags/")
        message = "\n".join(
            [
                f"game-exp archive {archive_id}",
                "",
                f"game-exp-experiment: {experiment_id}",
                f"game-exp-archive-id: {archive_id}",
                f"game-exp-mode: {mode}",
                f"game-exp-source-sha: {expected}",
            ]
        )
        run(
            ["git", "tag", "-a", tag_name, expected, "-m", message],
            cwd=work,
        )

        if mode == "ATOMIC_DELETE":
            proc = run(
                [
                    "git",
                    "push",
                    "--atomic",
                    f"--force-with-lease={branch_ref}:{expected}",
                    "origin",
                    f"{final_tag_ref}:{final_tag_ref}",
                    f":{branch_ref}",
                ],
                cwd=work,
                env=env,
                check=False,
            )
        elif mode == "RETAIN_BRANCH":
            proc = run(
                [
                    "git",
                    "push",
                    "origin",
                    f"{final_tag_ref}:{final_tag_ref}",
                ],
                cwd=work,
                env=env,
                check=False,
            )
        else:
            raise ArchiveRunnerError(f"unsupported archive mode {mode!r}")

    after = remote_state(repo, experiment_id, archive_id)
    return {
        "attempted": True,
        "push_returncode": proc.returncode,
        "push_stdout": proc.stdout[-4000:],
        "push_stderr": proc.stderr[-4000:],
        "before": before,
        "after": after,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--experiment-id", required=True)
    ap.add_argument("--mode", choices=("ATOMIC_DELETE", "RETAIN_BRANCH"), required=True)
    ap.add_argument("--ssh-key", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--run-attempt", required=True)
    ap.add_argument("--workflow-source-sha", required=True)
    ap.add_argument("--actor-login", required=True)
    args = ap.parse_args()

    writer_path = str(Path(__file__).with_name("trusted_writer.py"))
    actor_claim = f"github-workflow-dispatch:{args.actor_login}"

    current = status(args.repo, args.experiment_id, args.mode)
    if current["committed"]:
        print(json.dumps({"status": "COMMITTED", **current}, sort_keys=True))
        return 0

    if current["needs_prepare"]:
        info = payload_info(
            operation="prepare",
            experiment_id=args.experiment_id,
            mode=args.mode,
            archive_id=None,
            run_id=args.run_id,
            run_attempt=args.run_attempt,
            actor_claim=actor_claim,
        )
        submit_writer(
            repo=args.repo,
            info=info,
            authority="request",
            ssh_key=args.ssh_key,
            run_id=args.run_id,
            run_attempt=args.run_attempt,
            workflow_source_sha=args.workflow_source_sha,
            writer_path=writer_path,
        )
        current = status(args.repo, args.experiment_id, args.mode)

    archive_id = current.get("archive_id")
    archive = current.get("archive")
    if not isinstance(archive_id, str) or not isinstance(archive, dict):
        raise ArchiveRunnerError("archive prepare did not produce authoritative archive state")

    phase = archive.get("phase")
    if phase == "ABORTED":
        raise ArchiveRunnerError(f"archive {archive_id} was aborted")
    if phase == "PREPARED":
        info = payload_info(
            operation="claim",
            experiment_id=args.experiment_id,
            mode=args.mode,
            archive_id=archive_id,
            run_id=args.run_id,
            run_attempt=args.run_attempt,
            actor_claim=actor_claim,
        )
        submit_writer(
            repo=args.repo,
            info=info,
            authority="archive",
            ssh_key=args.ssh_key,
            run_id=args.run_id,
            run_attempt=args.run_attempt,
            workflow_source_sha=args.workflow_source_sha,
            writer_path=writer_path,
        )
        current = status(args.repo, args.experiment_id, args.mode)
        archive = current["archive"]
        phase = archive.get("phase")

    ref_evidence = None
    if phase in {"CLAIMED", "REF_CONFLICT"}:
        ref_evidence = mutate_refs(
            repo=args.repo,
            archive=archive,
            ssh_key=args.ssh_key,
        )
        info = payload_info(
            operation="observe",
            experiment_id=args.experiment_id,
            mode=args.mode,
            archive_id=archive_id,
            run_id=args.run_id,
            run_attempt=args.run_attempt,
            actor_claim=actor_claim,
        )
        submit_writer(
            repo=args.repo,
            info=info,
            authority="archive",
            ssh_key=args.ssh_key,
            run_id=args.run_id,
            run_attempt=args.run_attempt,
            workflow_source_sha=args.workflow_source_sha,
            writer_path=writer_path,
        )
        current = status(args.repo, args.experiment_id, args.mode)
        archive = current["archive"]
        phase = archive.get("phase")

    if phase == "REF_CONFLICT":
        raise ArchiveRunnerError(
            "archive refs are in REF_CONFLICT; the same archive must be recovered, not replaced"
            + (f"\nref_evidence={json.dumps(ref_evidence, sort_keys=True)}" if ref_evidence else "")
        )
    if phase == "REF_COMMITTED":
        info = payload_info(
            operation="commit",
            experiment_id=args.experiment_id,
            mode=args.mode,
            archive_id=archive_id,
            run_id=args.run_id,
            run_attempt=args.run_attempt,
            actor_claim=actor_claim,
        )
        submit_writer(
            repo=args.repo,
            info=info,
            authority="archive",
            ssh_key=args.ssh_key,
            run_id=args.run_id,
            run_attempt=args.run_attempt,
            workflow_source_sha=args.workflow_source_sha,
            writer_path=writer_path,
        )
        current = status(args.repo, args.experiment_id, args.mode)
        archive = current["archive"]
        phase = archive.get("phase")

    if not current.get("committed") or phase != "COMMITTED":
        raise ArchiveRunnerError(
            f"archive did not converge to COMMITTED; phase={phase!r}"
        )

    print(
        json.dumps(
            {
                "status": "COMMITTED",
                "experiment_id": args.experiment_id,
                "archive_id": archive_id,
                "mode": args.mode,
                "archive": archive,
                "ref_evidence": ref_evidence,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ArchiveRunnerError, archive_control.ArchiveControlError) as exc:
        print(
            json.dumps(
                {"status": "ARCHIVE_RUNNER_ERROR", "error": str(exc)},
                sort_keys=True,
            )
        )
        raise SystemExit(41)
