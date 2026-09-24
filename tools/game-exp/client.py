from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from protocol_core import (
    ProtocolError,
    build_operation_payload,
    digest_object,
    encode_payload_b64,
    new_request_id,
    validate_request_id,
)

RUN_URL_RE = re.compile(r"/actions/runs/(\d+)(?:$|[/?#])")


class ClientError(RuntimeError):
    pass


def _run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        proc = subprocess.run(
            args,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise ClientError(f"required command not found: {args[0]}") from exc
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
    proc = _run(["git", "rev-parse", "--git-common-dir"], check=False)
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
            ]
        )
        url = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        if not RUN_URL_RE.search(url):
            raise ClientError(f"workflow dispatch did not return a run URL: {proc.stdout!r}")
        return url

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
        expected_head = self.transport.ledger_head()
        payload_digest = digest_object(payload)
        workflow_url = self.transport.dispatch_writer(
            request_id=rid,
            expected_head=expected_head,
            payload_b64=encode_payload_b64(payload),
        )
        result = {
            "status": "ACCEPTED",
            "request_id": rid,
            "repo": self.transport.repo,
            "operation": operation,
            "expected_head": expected_head,
            "payload_digest": payload_digest,
            "workflow_url": workflow_url,
        }
        self._write_journal({**result, "payload": payload})
        return result

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
                if "REQUEST_ID_CONFLICT" in logs:
                    conflict_type = "REQUEST_ID_CONFLICT"
                elif "HEAD_CONFLICT" in logs:
                    conflict_type = "HEAD_CONFLICT"
                else:
                    conflict_type = None
                if conflict_type:
                    return {
                        "status": "CONFLICT",
                        "conflict_type": conflict_type,
                        "request_id": rid,
                        "repo": self.transport.repo,
                        "workflow": state,
                    }
                return {
                    "status": "REJECTED",
                    "request_id": rid,
                    "repo": self.transport.repo,
                    "workflow": state,
                }

        return {
            "status": "UNKNOWN",
            "reason": "no_remote_record_and_no_resolved_workflow",
            "request_id": rid,
            "repo": self.transport.repo,
        }

    def status(self) -> dict[str, Any]:
        return {
            "repo": self.transport.repo,
            "ledger_head": self.transport.ledger_head(),
        }

    def doctor(self) -> dict[str, Any]:
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

        overall = "PASS"
        if any(c["status"] == "FAIL" for c in checks):
            overall = "FAIL"
        elif any(c["status"] == "UNKNOWN" for c in checks):
            overall = "UNKNOWN"

        return {
            "status": overall,
            "repo": self.transport.repo,
            "checks": checks,
        }
