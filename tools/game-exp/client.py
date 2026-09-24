from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from protocol_core import (
    build_operation_payload,
    digest_object,
    encode_payload_b64,
    new_request_id,
    validate_request_id,
)

RUN_URL_RE = re.compile(r"/actions/runs/(\d+)(?:$|[/?#])")
EXPERIMENT_ID_RE = re.compile(r"^EXP-[1-9][0-9]*$")


class ClientError(RuntimeError):
    pass


class TransportUncertainError(ClientError):
    """The remote outcome may have happened, but the client cannot prove it yet."""


def _run(
    args: list[str],
    *,
    check: bool = True,
    timeout: float = 30.0,
) -> subprocess.CompletedProcess[str]:
    try:
        proc = subprocess.run(
            args,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise ClientError(f"required command not found: {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise TransportUncertainError(
            f"command timed out after {timeout:.0f}s: {' '.join(args[:3])}"
        ) from exc

    if check and proc.returncode != 0:
        raise ClientError(
            f"command failed ({proc.returncode}): {' '.join(args)}\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return proc


def _json_output(proc: subprocess.CompletedProcess[str]) -> Any:
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ClientError(f"command returned invalid JSON: {proc.stdout!r}") from exc


def _journal_root() -> Path:
    proc = _run(["git", "rev-parse", "--git-common-dir"], check=False, timeout=5)
    if proc.returncode == 0 and proc.stdout.strip():
        raw = Path(proc.stdout.strip())
        if not raw.is_absolute():
            raw = (Path.cwd() / raw).resolve()
        root = raw / "game-exp" / "requests"
    else:
        root = Path.home() / ".game-exp" / "requests"
    root.mkdir(parents=True, exist_ok=True)
    return root


class GitHubTransport:
    def __init__(self, repo: str | None = None):
        self.repo = repo or self._resolve_repo()

    def _resolve_repo(self) -> str:
        proc = _run(
            ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"]
        )
        repo = proc.stdout.strip()
        if "/" not in repo:
            raise ClientError("could not resolve GitHub repository")
        return repo

    def ledger_head(self) -> str:
        proc = _run(
            [
                "gh",
                "api",
                f"repos/{self.repo}/git/ref/heads/game-exp/ledger",
                "--jq",
                ".object.sha",
            ]
        )
        head = proc.stdout.strip()
        if not re.fullmatch(r"[0-9a-f]{40}", head):
            raise ClientError(f"invalid Ledger head returned by GitHub: {head!r}")
        return head

    def dispatch_writer(
        self,
        *,
        request_id: str,
        expected_head: str,
        payload_b64: str,
    ) -> str:
        proc = _run(
            [
                "gh",
                "workflow",
                "run",
                "game-exp-trusted-writer.yml",
                "--repo",
                self.repo,
                "--ref",
                "main",
                "-f",
                f"request_id={request_id}",
                "-f",
                f"expected_head={expected_head}",
                "-f",
                f"payload_b64={payload_b64}",
            ],
            check=False,
            timeout=30,
        )
        if proc.returncode != 0:
            raise TransportUncertainError(
                "workflow dispatch did not produce a provable result; reconcile by request_id"
            )

        url = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        if not RUN_URL_RE.search(url):
            raise TransportUncertainError(
                "workflow dispatch returned no run URL; outcome is uncertain"
            )
        return url

    def dispatch_initializer(self, experiment_id: str) -> str:
        if not EXPERIMENT_ID_RE.fullmatch(experiment_id):
            raise ClientError("experiment_id must be EXP-<positive integer>")
        proc = _run(
            [
                "gh",
                "workflow",
                "run",
                "game-exp-source-initializer.yml",
                "--repo",
                self.repo,
                "--ref",
                "main",
                "-f",
                f"experiment_id={experiment_id}",
            ],
            check=False,
            timeout=30,
        )
        if proc.returncode != 0:
            raise TransportUncertainError(
                "source initializer dispatch did not produce a provable result"
            )
        url = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        if not RUN_URL_RE.search(url):
            raise TransportUncertainError(
                "source initializer dispatch returned no run URL; outcome is uncertain"
            )
        return url

    def dispatch_rehearsal(self, experiment_id: str) -> str:
        if not EXPERIMENT_ID_RE.fullmatch(experiment_id):
            raise ClientError("experiment_id must be EXP-<positive integer>")
        proc = _run(
            [
                "gh",
                "workflow",
                "run",
                "game-exp-rehearsal.yml",
                "--repo",
                self.repo,
                "--ref",
                "main",
                "-f",
                f"experiment_id={experiment_id}",
            ],
            check=False,
            timeout=30,
        )
        if proc.returncode != 0:
            raise TransportUncertainError(
                "rehearsal dispatch did not produce a provable result"
            )
        url = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        if not RUN_URL_RE.search(url):
            raise TransportUncertainError(
                "rehearsal dispatch returned no run URL; outcome is uncertain"
            )
        return url

    def dispatch_integration(self, experiment_id: str) -> str:
        if not EXPERIMENT_ID_RE.fullmatch(experiment_id):
            raise ClientError("experiment_id must be EXP-<positive integer>")
        proc = _run(
            [
                "gh",
                "workflow",
                "run",
                "game-exp-integration.yml",
                "--repo",
                self.repo,
                "--ref",
                "main",
                "-f",
                f"experiment_id={experiment_id}",
            ],
            check=False,
            timeout=30,
        )
        if proc.returncode != 0:
            raise TransportUncertainError(
                "integration proposal dispatch did not produce a provable result"
            )
        url = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        if not RUN_URL_RE.search(url):
            raise TransportUncertainError(
                "integration proposal dispatch returned no run URL; outcome is uncertain"
            )
        return url

    def dispatch_integration_finalize(self, experiment_id: str, pr_number: str) -> str:
        if not EXPERIMENT_ID_RE.fullmatch(experiment_id):
            raise ClientError("experiment_id must be EXP-<positive integer>")
        if not re.fullmatch(r"[1-9][0-9]*", str(pr_number)):
            raise ClientError("pr_number must be a positive integer")
        proc = _run(
            [
                "gh",
                "workflow",
                "run",
                "game-exp-integration-finalize.yml",
                "--repo",
                self.repo,
                "--ref",
                "main",
                "-f",
                f"experiment_id={experiment_id}",
                "-f",
                f"pr_number={pr_number}",
            ],
            check=False,
            timeout=30,
        )
        if proc.returncode != 0:
            raise TransportUncertainError(
                "integration finalize dispatch did not produce a provable result"
            )
        url = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        if not RUN_URL_RE.search(url):
            raise TransportUncertainError(
                "integration finalize dispatch returned no run URL; outcome is uncertain"
            )
        return url

    def dispatch_archive(self, experiment_id: str, mode: str) -> str:
        if not EXPERIMENT_ID_RE.fullmatch(experiment_id):
            raise ClientError("experiment_id must be EXP-<positive integer>")
        if mode not in {"ATOMIC_DELETE", "RETAIN_BRANCH"}:
            raise ClientError("archive mode must be ATOMIC_DELETE or RETAIN_BRANCH")
        proc = _run(
            [
                "gh",
                "workflow",
                "run",
                "game-exp-archive.yml",
                "--repo",
                self.repo,
                "--ref",
                "main",
                "-f",
                f"experiment_id={experiment_id}",
                "-f",
                f"mode={mode}",
            ],
            check=False,
            timeout=30,
        )
        if proc.returncode != 0:
            raise TransportUncertainError(
                "archive dispatch did not produce a provable result"
            )
        url = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        if not RUN_URL_RE.search(url):
            raise TransportUncertainError(
                "archive dispatch returned no run URL; outcome is uncertain"
            )
        return url

    def ledger_json(self, path: str) -> dict[str, Any] | None:
        endpoint = (
            f"repos/{self.repo}/contents/{path}"
            "?ref=game-exp%2Fledger"
        )
        proc = _run(
            [
                "gh",
                "api",
                "-H",
                "Accept: application/vnd.github.raw+json",
                endpoint,
            ],
            check=False,
        )
        if proc.returncode != 0:
            text = (proc.stdout + "\n" + proc.stderr).lower()
            if "404" in text or "not found" in text:
                return None
            raise ClientError(
                f"failed reading Ledger JSON {path} ({proc.returncode}): {proc.stderr}"
            )
        try:
            value = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise ClientError(f"Ledger JSON is invalid at {path}") from exc
        if not isinstance(value, dict):
            raise ClientError(f"Ledger JSON must be an object at {path}")
        return value

    def git_ref(self, ref_path: str) -> dict[str, Any] | None:
        proc = _run(
            ["gh", "api", f"repos/{self.repo}/git/ref/{ref_path}"],
            check=False,
        )
        if proc.returncode != 0:
            text = (proc.stdout + "\n" + proc.stderr).lower()
            if "404" in text or "not found" in text:
                return None
            raise ClientError(
                f"failed reading Git ref {ref_path} ({proc.returncode}): {proc.stderr}"
            )
        value = _json_output(proc)
        if not isinstance(value, dict):
            raise ClientError(f"Git ref response must be an object: {ref_path}")
        return value

    def annotated_tag(self, tag_object_sha: str) -> dict[str, Any]:
        if not re.fullmatch(r"[0-9a-f]{40}", tag_object_sha):
            raise ClientError("annotated tag object SHA must be 40 lowercase hex")
        proc = _run(
            ["gh", "api", f"repos/{self.repo}/git/tags/{tag_object_sha}"],
        )
        value = _json_output(proc)
        if not isinstance(value, dict):
            raise ClientError("annotated tag response must be an object")
        return value

    def ledger_record(self, request_id: str) -> dict[str, Any] | None:
        validate_request_id(request_id)
        endpoint = (
            f"repos/{self.repo}/contents/operations/{request_id}.json"
            "?ref=game-exp%2Fledger"
        )
        proc = _run(
            [
                "gh",
                "api",
                "-H",
                "Accept: application/vnd.github.raw+json",
                endpoint,
            ],
            check=False,
        )
        if proc.returncode != 0:
            text = (proc.stdout + "\n" + proc.stderr).lower()
            if "404" in text or "not found" in text:
                return None
            raise ClientError(
                f"failed reading Ledger request record ({proc.returncode}): {proc.stderr}"
            )
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise ClientError("Ledger request record is not valid JSON") from exc

    def run_state(self, workflow_url: str) -> dict[str, Any] | None:
        match = RUN_URL_RE.search(workflow_url)
        if not match:
            return None
        proc = _run(
            [
                "gh",
                "run",
                "view",
                match.group(1),
                "--repo",
                self.repo,
                "--json",
                "status,conclusion,url",
            ],
            check=False,
        )
        if proc.returncode != 0:
            return None
        return _json_output(proc)

    def failed_run_logs(self, workflow_url: str) -> str:
        match = RUN_URL_RE.search(workflow_url)
        if not match:
            return ""
        proc = _run(
            [
                "gh",
                "run",
                "view",
                match.group(1),
                "--repo",
                self.repo,
                "--log-failed",
            ],
            check=False,
        )
        return proc.stdout + "\n" + proc.stderr

    def rulesets(self) -> list[dict[str, Any]]:
        proc = _run(["gh", "api", f"repos/{self.repo}/rulesets"])
        data = _json_output(proc)
        if not isinstance(data, list):
            raise ClientError("GitHub rulesets response is not a list")
        return data

    def deploy_keys(self) -> list[dict[str, Any]] | None:
        proc = _run(["gh", "api", f"repos/{self.repo}/keys"], check=False)
        if proc.returncode != 0:
            return None
        data = _json_output(proc)
        return data if isinstance(data, list) else None

    def secret_names(self) -> set[str] | None:
        proc = _run(
            ["gh", "secret", "list", "--repo", self.repo, "--json", "name"],
            check=False,
        )
        if proc.returncode != 0:
            return None
        data = _json_output(proc)
        return {row["name"] for row in data if isinstance(row, dict) and "name" in row}

    def immutable_releases(self) -> dict[str, Any] | None:
        proc = _run(
            [
                "gh",
                "api",
                f"repos/{self.repo}/immutable-releases",
                "-H",
                "X-GitHub-Api-Version: 2026-03-10",
            ],
            check=False,
        )
        if proc.returncode != 0:
            return None
        data = _json_output(proc)
        return data if isinstance(data, dict) else None


class GameExpClient:
    def __init__(self, transport: GitHubTransport):
        self.transport = transport

    def _journal_path(self, request_id: str) -> Path:
        validate_request_id(request_id)
        repo_key = self.transport.repo.replace("/", "__")
        path = _journal_root() / repo_key
        path.mkdir(parents=True, exist_ok=True)
        return path / f"{request_id}.json"

    def _write_journal(self, record: dict[str, Any]) -> None:
        path = self._journal_path(record["request_id"])
        path.write_text(
            json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

    def _read_journal(self, request_id: str) -> dict[str, Any] | None:
        path = self._journal_path(request_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ClientError(f"invalid local request journal {path}: {exc}") from exc
        return data if isinstance(data, dict) else None

    def submit(
        self,
        *,
        operation: str,
        input_value: dict[str, Any],
        preconditions: dict[str, Any] | None = None,
        actor_claim: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        rid = validate_request_id(request_id or new_request_id())
        payload = build_operation_payload(
            operation,
            input_value,
            preconditions=preconditions,
            actor_claim=actor_claim,
        )
        payload_digest = digest_object(payload)

        existing = self._read_journal(rid)
        if existing is not None:
            old_digest = existing.get("payload_digest")
            if old_digest != payload_digest:
                return {
                    "status": "CONFLICT",
                    "conflict_type": "LOCAL_REQUEST_ID_CONFLICT",
                    "request_id": rid,
                    "repo": self.transport.repo,
                    "expected_payload_digest": old_digest,
                    "new_payload_digest": payload_digest,
                }

            remote = self.transport.ledger_record(rid)
            if remote is not None:
                remote_digest = remote.get("payload_digest")
                if remote_digest != payload_digest:
                    result = {
                        "status": "CONFLICT",
                        "conflict_type": "REQUEST_ID_CONFLICT",
                        "request_id": rid,
                        "repo": self.transport.repo,
                        "expected_payload_digest": payload_digest,
                        "remote_payload_digest": remote_digest,
                        "record": remote,
                    }
                else:
                    result = {
                        "status": "COMMITTED",
                        "request_id": rid,
                        "repo": self.transport.repo,
                        "payload_digest": remote_digest,
                        "verified_against_local_request": True,
                        "record": remote,
                        "replayed": True,
                    }
                self._write_journal({**existing, **result})
                return result

            expected_head = existing["expected_head"]
        else:
            expected_head = self.transport.ledger_head()

        pending = {
            "status": "PENDING_DISPATCH",
            "request_id": rid,
            "repo": self.transport.repo,
            "operation": operation,
            "expected_head": expected_head,
            "payload_digest": payload_digest,
            "payload": payload,
        }
        self._write_journal({**existing, **pending} if existing else pending)

        try:
            workflow_url = self.transport.dispatch_writer(
                request_id=rid,
                expected_head=expected_head,
                payload_b64=encode_payload_b64(payload),
            )
        except TransportUncertainError as exc:
            result = {
                **pending,
                "status": "UNKNOWN",
                "reason": "dispatch_outcome_uncertain",
                "error": str(exc),
            }
            self._write_journal(result)
            return result

        result = {
            **pending,
            "status": "ACCEPTED",
            "workflow_url": workflow_url,
        }
        self._write_journal(result)
        return result

    def initialize(self, experiment_id: str) -> dict[str, Any]:
        if not EXPERIMENT_ID_RE.fullmatch(experiment_id):
            return {
                "status": "REJECTED",
                "repo": self.transport.repo,
                "experiment_id": experiment_id,
                "error": "experiment_id must be EXP-<positive integer>",
            }
        try:
            workflow_url = self.transport.dispatch_initializer(experiment_id)
        except TransportUncertainError as exc:
            return {
                "status": "UNKNOWN",
                "reason": "initializer_dispatch_outcome_uncertain",
                "repo": self.transport.repo,
                "experiment_id": experiment_id,
                "error": str(exc),
                "retry_safe": True,
            }
        return {
            "status": "ACCEPTED",
            "repo": self.transport.repo,
            "experiment_id": experiment_id,
            "workflow_url": workflow_url,
        }

    def rehearse(self, experiment_id: str) -> dict[str, Any]:
        if not EXPERIMENT_ID_RE.fullmatch(experiment_id):
            return {
                "status": "REJECTED",
                "repo": self.transport.repo,
                "experiment_id": experiment_id,
                "error": "experiment_id must be EXP-<positive integer>",
            }
        try:
            workflow_url = self.transport.dispatch_rehearsal(experiment_id)
        except TransportUncertainError as exc:
            return {
                "status": "UNKNOWN",
                "reason": "rehearsal_dispatch_outcome_uncertain",
                "repo": self.transport.repo,
                "experiment_id": experiment_id,
                "error": str(exc),
                "retry_safe": True,
            }
        return {
            "status": "ACCEPTED",
            "repo": self.transport.repo,
            "experiment_id": experiment_id,
            "workflow_url": workflow_url,
        }

    def integrate(self, experiment_id: str) -> dict[str, Any]:
        if not EXPERIMENT_ID_RE.fullmatch(experiment_id):
            return {
                "status": "REJECTED",
                "repo": self.transport.repo,
                "experiment_id": experiment_id,
                "error": "experiment_id must be EXP-<positive integer>",
            }
        try:
            workflow_url = self.transport.dispatch_integration(experiment_id)
        except TransportUncertainError as exc:
            return {
                "status": "UNKNOWN",
                "reason": "integration_dispatch_outcome_uncertain",
                "repo": self.transport.repo,
                "experiment_id": experiment_id,
                "error": str(exc),
                "retry_safe": True,
            }
        return {
            "status": "ACCEPTED",
            "repo": self.transport.repo,
            "experiment_id": experiment_id,
            "workflow_url": workflow_url,
        }

    def integrate_finalize(self, experiment_id: str, pr_number: str) -> dict[str, Any]:
        if not EXPERIMENT_ID_RE.fullmatch(experiment_id):
            return {
                "status": "REJECTED",
                "repo": self.transport.repo,
                "experiment_id": experiment_id,
                "error": "experiment_id must be EXP-<positive integer>",
            }
        if not re.fullmatch(r"[1-9][0-9]*", str(pr_number)):
            return {
                "status": "REJECTED",
                "repo": self.transport.repo,
                "experiment_id": experiment_id,
                "pr_number": str(pr_number),
                "error": "pr_number must be a positive integer",
            }
        try:
            workflow_url = self.transport.dispatch_integration_finalize(
                experiment_id,
                str(pr_number),
            )
        except TransportUncertainError as exc:
            return {
                "status": "UNKNOWN",
                "reason": "integration_finalize_dispatch_outcome_uncertain",
                "repo": self.transport.repo,
                "experiment_id": experiment_id,
                "pr_number": str(pr_number),
                "error": str(exc),
                "retry_safe": True,
            }
        return {
            "status": "ACCEPTED",
            "repo": self.transport.repo,
            "experiment_id": experiment_id,
            "pr_number": str(pr_number),
            "workflow_url": workflow_url,
        }

    def archive(self, experiment_id: str, mode: str) -> dict[str, Any]:
        if not EXPERIMENT_ID_RE.fullmatch(experiment_id):
            return {
                "status": "REJECTED",
                "repo": self.transport.repo,
                "experiment_id": experiment_id,
                "error": "experiment_id must be EXP-<positive integer>",
            }
        if mode not in {"ATOMIC_DELETE", "RETAIN_BRANCH"}:
            return {
                "status": "REJECTED",
                "repo": self.transport.repo,
                "experiment_id": experiment_id,
                "mode": mode,
                "error": "archive mode must be ATOMIC_DELETE or RETAIN_BRANCH",
            }
        try:
            workflow_url = self.transport.dispatch_archive(experiment_id, mode)
        except TransportUncertainError as exc:
            return {
                "status": "UNKNOWN",
                "reason": "archive_dispatch_outcome_uncertain",
                "repo": self.transport.repo,
                "experiment_id": experiment_id,
                "mode": mode,
                "error": str(exc),
                "retry_safe": True,
            }
        return {
            "status": "ACCEPTED",
            "repo": self.transport.repo,
            "experiment_id": experiment_id,
            "mode": mode,
            "workflow_url": workflow_url,
        }

    def archive_abort(
        self,
        experiment_id: str,
        archive_id: str,
        reason: str,
        *,
        actor_claim: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        if not EXPERIMENT_ID_RE.fullmatch(experiment_id):
            return {
                "status": "REJECTED",
                "repo": self.transport.repo,
                "experiment_id": experiment_id,
                "error": "experiment_id must be EXP-<positive integer>",
            }
        if not re.fullmatch(r"A-[1-9][0-9]*-[1-9][0-9]*", archive_id):
            return {
                "status": "REJECTED",
                "repo": self.transport.repo,
                "experiment_id": experiment_id,
                "archive_id": archive_id,
                "error": "archive_id must be A-<issue>-<sequence>",
            }
        if not isinstance(reason, str) or not reason.strip():
            return {
                "status": "REJECTED",
                "repo": self.transport.repo,
                "experiment_id": experiment_id,
                "archive_id": archive_id,
                "error": "archive abort reason is required",
            }
        return self.submit(
            operation="archive.abort",
            input_value={
                "experiment_id": experiment_id,
                "archive_id": archive_id,
                "reason": reason,
            },
            actor_claim=actor_claim,
            request_id=request_id,
        )

    def reconcile(self, request_id: str) -> dict[str, Any]:
        rid = validate_request_id(request_id)
        journal = self._read_journal(rid)
        expected_digest = journal.get("payload_digest") if journal else None
        record = self.transport.ledger_record(rid)

        if record is not None:
            remote_digest = record.get("payload_digest")
            if expected_digest is not None and remote_digest != expected_digest:
                result = {
                    "status": "CONFLICT",
                    "conflict_type": "REQUEST_ID_CONFLICT",
                    "request_id": rid,
                    "repo": self.transport.repo,
                    "expected_payload_digest": expected_digest,
                    "remote_payload_digest": remote_digest,
                    "record": record,
                }
            else:
                result = {
                    "status": "COMMITTED",
                    "request_id": rid,
                    "repo": self.transport.repo,
                    "payload_digest": remote_digest,
                    "verified_against_local_request": expected_digest is not None,
                    "record": record,
                }
            if journal:
                self._write_journal({**journal, **result})
            return result

        workflow_url = journal.get("workflow_url") if journal else None
        if workflow_url:
            state = self.transport.run_state(workflow_url)
            if state and state.get("status") in {"queued", "in_progress", "waiting", "requested"}:
                return {
                    "status": "ACCEPTED",
                    "request_id": rid,
                    "repo": self.transport.repo,
                    "workflow": state,
                }
            if state and state.get("status") == "completed":
                if state.get("conclusion") == "success":
                    return {
                        "status": "UNKNOWN",
                        "reason": "workflow_succeeded_but_no_ledger_record",
                        "request_id": rid,
                        "repo": self.transport.repo,
                        "workflow": state,
                    }
                logs = self.transport.failed_run_logs(workflow_url)
                domain_error = None
                if "REQUEST_ID_CONFLICT" in logs:
                    conflict_type = "REQUEST_ID_CONFLICT"
                elif "HEAD_CONFLICT" in logs:
                    conflict_type = "HEAD_CONFLICT"
                else:
                    match = re.search(
                        r'"status":"(DOMAIN_[A-Z_]+|EXPERIMENT_IDENTITY_CONFLICT)"',
                        logs,
                    )
                    conflict_type = match.group(1) if match and match.group(1).endswith("CONFLICT") else None
                    domain_error = match.group(1) if match else None
                if conflict_type:
                    return {
                        "status": "CONFLICT",
                        "conflict_type": conflict_type,
                        "request_id": rid,
                        "repo": self.transport.repo,
                        "workflow": state,
                    }
                result = {
                    "status": "REJECTED",
                    "request_id": rid,
                    "repo": self.transport.repo,
                    "workflow": state,
                }
                if domain_error:
                    result["domain_error"] = domain_error
                return result

        return {
            "status": "UNKNOWN",
            "reason": "no_remote_record_and_no_resolved_workflow",
            "request_id": rid,
            "repo": self.transport.repo,
            "retry_safe_with_same_request_id": journal is not None,
            "expected_head": journal.get("expected_head") if journal else None,
            "payload_digest": expected_digest,
        }

    def status(self) -> dict[str, Any]:
        return {
            "status": "PASS",
            "repo": self.transport.repo,
            "ledger_head": self.transport.ledger_head(),
        }

    def archive_health(self, experiment_id: str) -> dict[str, Any]:
        if not EXPERIMENT_ID_RE.fullmatch(experiment_id):
            return {
                "status": "FAIL",
                "code": "ARCHIVE_EXPERIMENT_ID_INVALID",
                "experiment_id": experiment_id,
            }
        issue = experiment_id.removeprefix("EXP-")
        state = self.transport.ledger_json(
            f"experiments/{experiment_id}/state.json"
        )
        if state is None:
            return {
                "status": "FAIL",
                "code": "ARCHIVE_STATE_MISSING",
                "experiment_id": experiment_id,
            }
        if state.get("lifecycle") != "ARCHIVED":
            return {
                "status": "UNKNOWN",
                "code": "EXPERIMENT_NOT_ARCHIVED",
                "experiment_id": experiment_id,
                "lifecycle": state.get("lifecycle"),
            }
        archive_id = state.get("current_archive_id")
        if not isinstance(archive_id, str):
            return {
                "status": "FAIL",
                "code": "ARCHIVE_ID_MISSING",
                "experiment_id": experiment_id,
            }
        record = self.transport.ledger_json(
            f"experiments/{experiment_id}/archives/{archive_id}.json"
        )
        if not isinstance(record, dict) or record.get("phase") != "COMMITTED":
            return {
                "status": "FAIL",
                "code": "ARCHIVE_RECORD_INVALID",
                "experiment_id": experiment_id,
                "archive_id": archive_id,
            }

        mode = record.get("mode")
        expected = record.get("expected_branch_sha")
        branch_ref = record.get("branch_ref")
        final_ref = record.get("final_tag_ref")
        if (
            mode not in {"ATOMIC_DELETE", "RETAIN_BRANCH"}
            or not isinstance(expected, str)
            or not re.fullmatch(r"[0-9a-f]{40}", expected)
            or branch_ref != f"refs/heads/exp/{issue}"
            or final_ref != f"refs/tags/exp-final/{issue}"
        ):
            return {
                "status": "FAIL",
                "code": "ARCHIVE_RECORD_INVALID",
                "experiment_id": experiment_id,
                "archive_id": archive_id,
            }

        final = self.transport.git_ref(f"tags/exp-final/{issue}")
        if final is None:
            return {
                "status": "FAIL",
                "code": "ARCHIVE_FINAL_TAG_MISSING",
                "experiment_id": experiment_id,
                "archive_id": archive_id,
            }
        obj = final.get("object") or {}
        if obj.get("type") != "tag" or not isinstance(obj.get("sha"), str):
            return {
                "status": "FAIL",
                "code": "ARCHIVE_FINAL_TAG_NOT_ANNOTATED",
                "experiment_id": experiment_id,
                "archive_id": archive_id,
            }
        tag = self.transport.annotated_tag(obj["sha"])
        target = tag.get("object") or {}
        if target.get("type") != "commit" or target.get("sha") != expected:
            return {
                "status": "FAIL",
                "code": "ARCHIVE_FINAL_TAG_TARGET_MISMATCH",
                "experiment_id": experiment_id,
                "archive_id": archive_id,
                "expected": expected,
                "actual": target.get("sha"),
            }
        metadata: dict[str, str] = {}
        message = tag.get("message")
        if not isinstance(message, str):
            return {
                "status": "FAIL",
                "code": "ARCHIVE_FINAL_TAG_METADATA_MISSING",
                "experiment_id": experiment_id,
                "archive_id": archive_id,
            }
        for line in message.splitlines():
            if ": " in line:
                key, value = line.split(": ", 1)
                metadata[key.strip()] = value.strip()
        expected_metadata = {
            "game-exp-experiment": experiment_id,
            "game-exp-archive-id": archive_id,
            "game-exp-mode": mode,
            "game-exp-source-sha": expected,
        }
        if any(metadata.get(k) != v for k, v in expected_metadata.items()):
            return {
                "status": "FAIL",
                "code": "ARCHIVE_FINAL_TAG_METADATA_MISMATCH",
                "experiment_id": experiment_id,
                "archive_id": archive_id,
                "metadata": metadata,
            }

        branch = self.transport.git_ref(f"heads/exp/{issue}")
        branch_sha = None
        if branch is not None:
            bobj = branch.get("object") or {}
            if bobj.get("type") != "commit" or not isinstance(bobj.get("sha"), str):
                return {
                    "status": "FAIL",
                    "code": "ARCHIVE_BRANCH_REF_INVALID",
                    "experiment_id": experiment_id,
                    "archive_id": archive_id,
                }
            branch_sha = bobj["sha"]

        base = {
            "experiment_id": experiment_id,
            "archive_id": archive_id,
            "mode": mode,
            "official_snapshot_sha": expected,
            "final_tag_ref": final_ref,
            "branch_ref": branch_ref,
            "branch_sha": branch_sha,
        }
        if mode == "ATOMIC_DELETE":
            if branch_sha is None:
                return {"status": "PASS", "code": "ARCHIVE_HEALTHY", **base}
            return {
                "status": "FAIL",
                "code": "POST_ARCHIVE_BRANCH_RESURRECTED",
                **base,
            }

        if branch_sha is None:
            return {
                "status": "FAIL",
                "code": "POST_ARCHIVE_BRANCH_MISSING",
                **base,
            }
        if branch_sha == expected:
            return {"status": "PASS", "code": "ARCHIVE_HEALTHY", **base}
        return {
            "status": "WARN",
            "code": "POST_ARCHIVE_BRANCH_DRIFT",
            **base,
        }

    def doctor(self, experiment_id: str | None = None) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []

        def add(name: str, status: str, detail: Any = None):
            checks.append({"name": name, "status": status, "detail": detail})

        try:
            head = self.transport.ledger_head()
            add("ledger_ref", "PASS", head)
        except Exception as exc:
            add("ledger_ref", "FAIL", str(exc))

        try:
            rules = self.transport.rulesets()
            active_names = {
                row.get("name")
                for row in rules
                if row.get("enforcement") == "active"
            }
            required = {
                "game-exp ledger",
                "game-exp experiment branches",
                "game-exp immutable refs",
                "game-exp protected main",
            }
            missing = sorted(required - active_names)
            add("rulesets", "PASS" if not missing else "FAIL", {"missing": missing})
        except Exception as exc:
            add("rulesets", "UNKNOWN", str(exc))

        keys = self.transport.deploy_keys()
        if keys is None:
            add("trusted_writer_deploy_key", "UNKNOWN", "cannot read deploy keys")
        else:
            write_keys = [k for k in keys if not k.get("read_only", True)]
            exact = [k for k in write_keys if k.get("title") == "game-exp trusted writer"]
            add(
                "trusted_writer_deploy_key",
                "PASS" if len(exact) == 1 and len(write_keys) == 1 else "FAIL",
                {
                    "matching_keys": len(exact),
                    "write_keys": len(write_keys),
                    "titles": [k.get("title") for k in write_keys],
                },
            )

        secrets = self.transport.secret_names()
        if secrets is None:
            add("trusted_writer_secret", "UNKNOWN", "cannot list repository secrets")
        else:
            add(
                "trusted_writer_secret",
                "PASS" if "GAME_EXP_WRITER_KEY" in secrets else "FAIL",
                None,
            )

        immutable = self.transport.immutable_releases()
        if immutable is None:
            add("immutable_releases", "UNKNOWN", "cannot query setting")
        else:
            add(
                "immutable_releases",
                "PASS" if immutable.get("enabled") is True else "FAIL",
                immutable,
            )

        archive_health = None
        if experiment_id is not None:
            try:
                archive_health = self.archive_health(experiment_id)
                add(
                    "archive_health",
                    archive_health["status"],
                    archive_health,
                )
            except Exception as exc:
                add("archive_health", "UNKNOWN", str(exc))

        overall = "PASS"
        if any(c["status"] == "FAIL" for c in checks):
            overall = "FAIL"
        elif any(c["status"] == "UNKNOWN" for c in checks):
            overall = "UNKNOWN"
        elif any(c["status"] == "WARN" for c in checks):
            overall = "WARN"

        return {
            "status": overall,
            "repo": self.transport.repo,
            "experiment_id": experiment_id,
            "checks": checks,
        }
