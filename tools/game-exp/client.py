from __future__ import annotations

import base64
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from protocol_core import (
    ASYNC_EXECUTION_ACTIONS,
    build_operation_payload,
    contract_descriptor,
    digest_object,
    encode_payload_b64,
    new_request_id,
    validate_request_id,
)

RUN_URL_RE = re.compile(r"/actions/runs/(\d+)(?:$|[/?#])")
EXPERIMENT_ID_RE = re.compile(r"^EXP-[1-9][0-9]*$")

WORKFLOW_EXECUTION_SPECS: dict[str, tuple[str, tuple[str, ...]]] = {
    "initialize": ("game-exp-source-initializer.yml", ()),
    "candidate_build": ("game-exp-candidate.yml", ()),
    "rehearse": ("game-exp-rehearsal.yml", ()),
    "integrate": ("game-exp-integration.yml", ()),
    "integrate_finalize": ("game-exp-integration-finalize.yml", ("pr_number",)),
    "archive": ("game-exp-archive.yml", ("mode",)),
}


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

    def ledger_paths(self, ref: str) -> list[str]:
        if not re.fullmatch(r"[0-9a-f]{40}", ref):
            raise ClientError("Ledger tree ref must be a 40-character commit SHA")
        proc = _run(
            [
                "gh",
                "api",
                f"repos/{self.repo}/git/trees/{ref}?recursive=1",
            ]
        )
        value = _json_output(proc)
        if not isinstance(value, dict):
            raise ClientError("Ledger tree response must be an object")
        if value.get("truncated") is True:
            raise ClientError("Ledger recursive tree response is truncated")
        tree = value.get("tree")
        if not isinstance(tree, list):
            raise ClientError("Ledger tree response is missing tree entries")
        paths: list[str] = []
        for row in tree:
            if (
                isinstance(row, dict)
                and row.get("type") == "blob"
                and isinstance(row.get("path"), str)
            ):
                paths.append(row["path"])
        return sorted(paths)

    def dispatch_writer(
        self,
        *,
        request_id: str,
        expected_head: str,
        payload_b64: str,
        payload_digest: str,
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
                "-f",
                f"payload_digest={payload_digest}",
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

    def find_request_runs(self, request_id: str) -> list[dict[str, Any]]:
        validate_request_id(request_id)
        proc = _run(
            [
                "gh",
                "run",
                "list",
                "--repo",
                self.repo,
                "--workflow",
                "game-exp-trusted-writer.yml",
                "--limit",
                "100",
                "--json",
                "databaseId,displayTitle,status,conclusion,url,createdAt",
            ],
            check=False,
            timeout=30,
        )
        if proc.returncode != 0:
            return []
        rows = _json_output(proc)
        if not isinstance(rows, list):
            return []
        prefix = f"game-exp:request:{request_id}:"
        matches: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            title = row.get("displayTitle")
            if not isinstance(title, str) or not title.startswith(prefix):
                continue
            suffix = title[len(prefix):]
            match = re.fullmatch(r"(sha256:[0-9a-f]{64}):([0-9a-f]{40})", suffix)
            if match is None:
                continue
            matches.append(
                {
                    **row,
                    "payloadDigest": match.group(1),
                    "expectedHead": match.group(2),
                }
            )
        matches.sort(key=lambda row: int(row.get("databaseId") or 0))
        return matches

    def dispatch_execution(
        self,
        *,
        action: str,
        experiment_id: str,
        request_id: str,
        arguments: dict[str, Any] | None = None,
    ) -> str:
        if action not in WORKFLOW_EXECUTION_SPECS:
            raise ClientError(f"unsupported async execution action: {action}")
        if not EXPERIMENT_ID_RE.fullmatch(experiment_id):
            raise ClientError("experiment_id must be EXP-<positive integer>")
        validate_request_id(request_id)
        workflow, argument_names = WORKFLOW_EXECUTION_SPECS[action]
        values = arguments or {}
        if set(values) != set(argument_names):
            raise ClientError(
                f"execution arguments mismatch for {action}: "
                f"expected={sorted(argument_names)} actual={sorted(values)}"
            )
        command = [
            "gh",
            "workflow",
            "run",
            workflow,
            "--repo",
            self.repo,
            "--ref",
            "main",
            "-f",
            f"experiment_id={experiment_id}",
            "-f",
            f"request_id={request_id}",
        ]
        for name in argument_names:
            command.extend(["-f", f"{name}={values[name]}"])
        proc = _run(command, check=False, timeout=30)
        if proc.returncode != 0:
            raise TransportUncertainError(
                f"{action} workflow dispatch did not produce a provable result"
            )
        url = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        if not RUN_URL_RE.search(url):
            raise TransportUncertainError(
                f"{action} workflow dispatch returned no run URL; outcome is uncertain"
            )
        return url

    def find_execution_run(
        self,
        *,
        action: str,
        request_id: str,
    ) -> dict[str, Any] | None:
        if action not in WORKFLOW_EXECUTION_SPECS:
            raise ClientError(f"unsupported async execution action: {action}")
        validate_request_id(request_id)
        workflow, _ = WORKFLOW_EXECUTION_SPECS[action]
        proc = _run(
            [
                "gh",
                "run",
                "list",
                "--repo",
                self.repo,
                "--workflow",
                workflow,
                "--limit",
                "100",
                "--json",
                "databaseId,displayTitle,status,conclusion,url,createdAt",
            ],
            check=False,
            timeout=30,
        )
        if proc.returncode != 0:
            return None
        rows = _json_output(proc)
        if not isinstance(rows, list):
            return None
        expected_title = f"game-exp:{action}:{request_id}"
        matches = [
            row
            for row in rows
            if isinstance(row, dict) and row.get("displayTitle") == expected_title
        ]
        if not matches:
            return None
        matches.sort(
            key=lambda row: int(row.get("databaseId") or 0),
        )
        return matches[0]

    def dispatch_initializer(self, experiment_id: str, request_id: str) -> str:
        return self.dispatch_execution(
            action="initialize",
            experiment_id=experiment_id,
            request_id=request_id,
        )

    def dispatch_candidate(self, experiment_id: str, request_id: str) -> str:
        return self.dispatch_execution(
            action="candidate_build",
            experiment_id=experiment_id,
            request_id=request_id,
        )

    def dispatch_rehearsal(self, experiment_id: str, request_id: str) -> str:
        return self.dispatch_execution(
            action="rehearse",
            experiment_id=experiment_id,
            request_id=request_id,
        )

    def dispatch_integration(self, experiment_id: str, request_id: str) -> str:
        return self.dispatch_execution(
            action="integrate",
            experiment_id=experiment_id,
            request_id=request_id,
        )

    def dispatch_integration_finalize(
        self,
        experiment_id: str,
        pr_number: str,
        request_id: str,
    ) -> str:
        return self.dispatch_execution(
            action="integrate_finalize",
            experiment_id=experiment_id,
            request_id=request_id,
            arguments={"pr_number": str(pr_number)},
        )

    def dispatch_archive(
        self,
        experiment_id: str,
        mode: str,
        request_id: str,
    ) -> str:
        return self.dispatch_execution(
            action="archive",
            experiment_id=experiment_id,
            request_id=request_id,
            arguments={"mode": mode},
        )

    def ledger_json(
        self,
        path: str,
        *,
        ref: str | None = None,
    ) -> dict[str, Any] | None:
        ref_value = ref or "game-exp%2Fledger"
        if ref is not None and not re.fullmatch(r"[0-9a-f]{40}", ref):
            raise ClientError("Ledger JSON ref must be a 40-character commit SHA")
        endpoint = f"repos/{self.repo}/contents/{path}?ref={ref_value}"
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

    def repository_access(self) -> dict[str, Any]:
        proc = _run(["gh", "api", f"repos/{self.repo}"], check=False)
        if proc.returncode != 0:
            text = (proc.stdout + "\n" + proc.stderr).lower()
            reason = "repository_not_accessible"
            if "401" in text or "authentication" in text:
                reason = "authentication_required"
            elif "403" in text:
                reason = "access_forbidden"
            elif "404" in text or "not found" in text:
                reason = "repository_not_found_or_hidden"
            return {
                "status": "NO_ACCESS",
                "can_read": False,
                "can_write": False,
                "can_admin": False,
                "admin_coverage": "NONE",
                "reason": reason,
            }
        data = _json_output(proc)
        permissions = data.get("permissions") if isinstance(data, dict) else None
        if not isinstance(permissions, dict):
            return {
                "status": "UNKNOWN",
                "can_read": True,
                "can_write": False,
                "can_admin": False,
                "admin_coverage": "UNKNOWN",
                "reason": "permissions_not_exposed",
            }
        can_read = bool(permissions.get("pull"))
        can_write = bool(
            permissions.get("push")
            or permissions.get("maintain")
            or permissions.get("admin")
        )
        can_admin = bool(permissions.get("admin"))
        if can_admin:
            status = "ADMIN"
            admin_coverage = "FULL"
        elif can_write:
            status = "WRITE"
            admin_coverage = "PARTIAL"
        elif can_read:
            status = "READ_ONLY"
            admin_coverage = "PARTIAL"
        else:
            status = "NO_ACCESS"
            admin_coverage = "NONE"
        return {
            "status": status,
            "can_read": can_read,
            "can_write": can_write,
            "can_admin": can_admin,
            "admin_coverage": admin_coverage,
            "reason": None,
        }

    def collaborator_permission(self, login: str) -> str | None:
        if not isinstance(login, str) or not login.strip():
            return None
        proc = _run(
            [
                "gh",
                "api",
                f"repos/{self.repo}/collaborators/{login.strip()}/permission",
            ],
            check=False,
        )
        if proc.returncode != 0:
            return None
        value = _json_output(proc)
        permission = value.get("permission") if isinstance(value, dict) else None
        return permission if isinstance(permission, str) else None

    def branch_contributors(self, ref: str) -> dict[str, Any]:
        proc = _run(
            ["gh", "api", f"repos/{self.repo}/commits?sha={ref}&per_page=100"],
            check=False,
        )
        if proc.returncode != 0:
            return {
                "contributors": [],
                "source": "github_commits",
                "complete": False,
            }
        data = _json_output(proc)
        if not isinstance(data, list):
            return {
                "contributors": [],
                "source": "github_commits",
                "complete": False,
            }
        logins: list[str] = []
        for row in data:
            if not isinstance(row, dict):
                continue
            author = row.get("author")
            login = author.get("login") if isinstance(author, dict) else None
            if isinstance(login, str) and login and login not in logins:
                logins.append(login)
        return {
            "contributors": logins,
            "source": "github_commits",
            "complete": len(data) < 100,
        }


class GameExpClient:
    def __init__(self, transport: GitHubTransport):
        self.transport = transport

    def access_check(self) -> dict[str, Any]:
        access = self.transport.repository_access()
        status = access.get("status")
        message_zh = {
            "NO_ACCESS": "无法访问该 GitHub 仓库。请检查登录、仓库授权或切换到有权限的仓库。",
            "READ_ONLY": "当前只有读取权限：可以查看 game-exp 面板，但不能创建或推进实验。请获得仓库写入权限后继续。",
            "WRITE": "当前具有读写权限，可以使用 game-exp；部分管理员级检查可能不可见。",
            "ADMIN": "当前具有完整仓库管理权限。",
            "UNKNOWN": "可以读取仓库，但当前连接未暴露完整权限信息。",
        }.get(str(status), "仓库权限状态未知。")
        return {
            "status": "PASS" if status in {"READ_ONLY", "WRITE", "ADMIN"} else status,
            "repo": self.transport.repo,
            "access": access,
            "message_zh": message_zh,
            "can_create_experiment": bool(access.get("can_write")),
        }

    def capabilities(self) -> dict[str, Any]:
        access = self.transport.repository_access()
        return {
            "status": "PASS" if access.get("can_read") else access.get("status", "UNKNOWN"),
            "repo": self.transport.repo,
            "contract": contract_descriptor(),
            "features": {
                "board": True,
                "request_recovery": True,
                "async_execution_claims": True,
                "notifications": True,
                "notification_cursors": True,
                "dependency_review_hints": True,
                "godot_handoff": True,
                "archive_recovery": True,
            },
            "queries": [
                "status",
                "access_check",
                "capabilities",
                "board",
                "experiment_get",
                "subject_panel",
                "experiment_panel",
                "operation_get",
                "notifications",
                "prototype_handoff",
            ],
            "commands": [
                "experiment.bind",
                "execution.claim",
                "review.record",
                "experiment.decision",
                "archive.abort",
                *list(ASYNC_EXECUTION_ACTIONS),
            ],
            "recovery": {
                "operation_get": True,
                "resume_execution": True,
                "cross_interface": True,
            },
            "access_snapshot": access,
            "access_snapshot_authoritative_for_execution": False,
            "note_zh": "权限快照仅用于提示；每次写操作仍由可信执行边界重新验证身份和权限。",
        }

    def _contributors_for_item(self, item: dict[str, Any]) -> dict[str, Any]:
        resolver = getattr(self.transport, "branch_contributors", None)
        if not callable(resolver):
            return {
                "contributors": [],
                "source": "unavailable",
                "complete": False,
            }
        ref = item.get("final_tag_ref") if item.get("lifecycle") == "ARCHIVED" else item.get("branch_ref")
        if not isinstance(ref, str) or not ref:
            return {
                "contributors": [],
                "source": "unavailable",
                "complete": False,
            }
        short_ref = ref.removeprefix("refs/heads/").removeprefix("refs/tags/")
        try:
            return resolver(short_ref)
        except Exception:
            return {
                "contributors": [],
                "source": "github_commits",
                "complete": False,
            }

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

    def _request_run_projection(
        self,
        request_id: str,
        run: dict[str, Any],
        *,
        expected_digest: str | None = None,
        expected_head: str | None = None,
    ) -> dict[str, Any]:
        run_digest = run.get("payloadDigest")
        run_head = run.get("expectedHead")
        base = {
            "request_id": request_id,
            "repo": self.transport.repo,
            "payload_digest": run_digest,
            "expected_head": run_head,
            "workflow": run,
        }
        if expected_digest is not None and run_digest != expected_digest:
            return {
                "status": "CONFLICT",
                "conflict_type": "REQUEST_ID_CONFLICT",
                "expected_payload_digest": expected_digest,
                "remote_payload_digest": run_digest,
                **base,
            }
        if expected_head is not None and run_head != expected_head:
            return {
                "status": "CONFLICT",
                "conflict_type": "REQUEST_PRECONDITION_CONFLICT",
                "expected_ledger_head": expected_head,
                "remote_expected_head": run_head,
                **base,
            }

        state = run.get("status")
        conclusion = run.get("conclusion")
        if state in {"queued", "in_progress", "waiting", "requested", "pending"}:
            return {
                "status": "ACCEPTED",
                "operation_status": "WRITER_RUNNING",
                **base,
            }
        if state != "completed":
            return {
                "status": "UNKNOWN",
                "reason": "writer_run_state_unknown",
                **base,
            }
        if conclusion == "success":
            return {
                "status": "UNKNOWN",
                "reason": "workflow_succeeded_but_no_ledger_record",
                "operation_status": "WRITER_COMPLETED",
                **base,
            }

        workflow_url = run.get("url")
        logs = (
            self.transport.failed_run_logs(workflow_url)
            if isinstance(workflow_url, str)
            else ""
        )
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
            conflict_type = (
                match.group(1)
                if match and match.group(1).endswith("CONFLICT")
                else None
            )
            domain_error = match.group(1) if match else None
        if conflict_type:
            return {
                "status": "CONFLICT",
                "conflict_type": conflict_type,
                **base,
            }
        result = {
            "status": "REJECTED",
            "operation_status": "WRITER_FAILED",
            **base,
        }
        if domain_error:
            result["domain_error"] = domain_error
        return result

    def _discover_request_runs(self, request_id: str) -> list[dict[str, Any]]:
        resolver = getattr(self.transport, "find_request_runs", None)
        if not callable(resolver):
            return []
        try:
            rows = resolver(request_id)
        except Exception:
            return []
        return rows if isinstance(rows, list) else []

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
            if existing is not None:
                self._write_journal({**existing, **result})
            return result

        runs = self._discover_request_runs(rid)
        if runs:
            first = runs[0]
            result = self._request_run_projection(
                rid,
                first,
                expected_digest=payload_digest,
                expected_head=(
                    existing.get("expected_head")
                    if isinstance(existing, dict)
                    else None
                ),
            )
            conflicting_attempts = [
                {
                    "databaseId": row.get("databaseId"),
                    "payloadDigest": row.get("payloadDigest"),
                    "expectedHead": row.get("expectedHead"),
                }
                for row in runs[1:]
                if (
                    row.get("payloadDigest") != first.get("payloadDigest")
                    or row.get("expectedHead") != first.get("expectedHead")
                )
            ]
            if conflicting_attempts:
                result["conflicting_attempts"] = conflicting_attempts
            if existing is not None:
                self._write_journal({**existing, **result})
            return result

        expected_head = (
            existing["expected_head"]
            if isinstance(existing, dict) and isinstance(existing.get("expected_head"), str)
            else self.transport.ledger_head()
        )
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
                payload_digest=payload_digest,
            )
        except TransportUncertainError as exc:
            result = {
                **pending,
                "status": "UNKNOWN",
                "reason": "dispatch_outcome_uncertain",
                "error": str(exc),
                "recovery": "query the same request_id before any retry",
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

    def bind(
        self,
        manifest: dict[str, Any],
        *,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        manifest_request_id = manifest.get("operation_id")
        if not isinstance(manifest_request_id, str) or not manifest_request_id:
            return {
                "status": "REJECTED",
                "repo": self.transport.repo,
                "error": "manifest.operation_id is required",
            }
        if request_id is not None and request_id != manifest_request_id:
            return {
                "status": "CONFLICT",
                "conflict_type": "REQUEST_ID_MANIFEST_MISMATCH",
                "repo": self.transport.repo,
                "request_id": request_id,
                "manifest_operation_id": manifest_request_id,
            }
        return self.submit(
            operation="experiment.bind",
            input_value={"manifest": manifest},
            request_id=manifest_request_id,
        )

    def review_record(
        self,
        experiment_id: str,
        *,
        outcome: str,
        notes: str,
        request_id: str,
        candidate_id: str | None = None,
    ) -> dict[str, Any]:
        if candidate_id is None:
            projection = self.experiment_get(experiment_id)
            if projection.get("status") != "PASS":
                return projection
            candidate_id = projection.get("state", {}).get("current_candidate_id")
            if not isinstance(candidate_id, str) or not candidate_id:
                return {
                    "status": "REJECTED",
                    "repo": self.transport.repo,
                    "experiment_id": experiment_id,
                    "request_id": request_id,
                    "error": "experiment has no current Candidate",
                }
        return self.submit(
            operation="review.record",
            input_value={
                "experiment_id": experiment_id,
                "candidate_id": candidate_id,
                "outcome": outcome,
                "notes": notes,
            },
            request_id=request_id,
        )

    def decision_submit(
        self,
        experiment_id: str,
        *,
        to_state: str,
        reason: str,
        request_id: str,
        previous_decision_id: str | None = None,
    ) -> dict[str, Any]:
        if previous_decision_id is None:
            projection = self.experiment_get(experiment_id)
            if projection.get("status") != "PASS":
                return projection
            previous_decision_id = projection.get("state", {}).get("last_decision_id")
        return self.submit(
            operation="experiment.decision",
            input_value={
                "experiment_id": experiment_id,
                "to_state": to_state,
                "previous_decision_id": previous_decision_id,
                "reason": reason,
            },
            request_id=request_id,
        )

    def experiment_get(self, experiment_id: str) -> dict[str, Any]:
        if not EXPERIMENT_ID_RE.fullmatch(experiment_id):
            return {
                "status": "REJECTED",
                "repo": self.transport.repo,
                "experiment_id": experiment_id,
                "error": "experiment_id must be EXP-<positive integer>",
            }

        state = self.transport.ledger_json(
            f"experiments/{experiment_id}/state.json"
        )
        if state is None:
            return {
                "status": "UNKNOWN",
                "repo": self.transport.repo,
                "experiment_id": experiment_id,
                "reason": "experiment_not_found_in_ledger",
            }
        binding = self.transport.ledger_json(
            f"experiments/{experiment_id}/binding.json"
        )
        manifest = self.transport.ledger_json(
            f"experiments/{experiment_id}/manifest.json"
        )

        result: dict[str, Any] = {
            "status": "PASS",
            "repo": self.transport.repo,
            "experiment_id": experiment_id,
            "state": state,
            "binding": binding,
            "manifest": manifest,
        }
        current_paths = {
            "candidate": (
                state.get("current_candidate_id"),
                "candidates",
            ),
            "review": (
                state.get("current_review_id"),
                "reviews",
            ),
            "rehearsal": (
                state.get("current_rehearsal_id"),
                "rehearsals",
            ),
            "integration": (
                state.get("current_integration_id"),
                "integrations",
            ),
            "archive": (
                state.get("current_archive_id"),
                "archives",
            ),
        }
        for key, (object_id, folder) in current_paths.items():
            if isinstance(object_id, str) and object_id:
                result[key] = self.transport.ledger_json(
                    f"experiments/{experiment_id}/{folder}/{object_id}.json"
                )
            else:
                result[key] = None
        return result

    @staticmethod
    def _board_prototype_name(manifest: dict[str, Any]) -> str | None:
        scope = manifest.get("scope")
        if not isinstance(scope, dict):
            return None
        allowed = scope.get("allowed")
        if not isinstance(allowed, list):
            return None

        names: set[str] = set()
        ambiguous_game_scope = False
        for pattern in allowed:
            if not isinstance(pattern, str):
                continue
            parts = pattern.split("/")
            if not parts or parts[0] != "games":
                continue
            if len(parts) < 2:
                ambiguous_game_scope = True
                continue
            segment = parts[1]
            if not segment or any(ch in segment for ch in "*?[]{}"):
                ambiguous_game_scope = True
                continue
            names.add(segment)

        if len(names) == 1:
            return next(iter(names))
        if len(names) > 1:
            return " / ".join(sorted(names))
        if ambiguous_game_scope:
            return "仓库级/未指定原型"
        return None

    @classmethod
    def _board_subject(cls, manifest: dict[str, Any]) -> dict[str, Any]:
        subject = manifest.get("subject")
        if isinstance(subject, dict):
            subject_type = subject.get("type")
            subject_id = subject.get("id")
            subject_name = subject.get("name")
            root_path = subject.get("root_path")
            if all(
                isinstance(value, str) and value
                for value in (
                    subject_type,
                    subject_id,
                    subject_name,
                    root_path,
                )
            ):
                return {
                    "type": subject_type,
                    "id": subject_id,
                    "name": subject_name,
                    "root_path": root_path,
                    "source": "manifest",
                }

        prototype_name = cls._board_prototype_name(manifest)
        if prototype_name is None or prototype_name == "仓库级/未指定原型":
            return {
                "type": "repository",
                "id": "repository",
                "name": "仓库级/未指定原型",
                "root_path": None,
                "source": "scope-fallback",
            }
        if " / " in prototype_name:
            return {
                "type": "multi-prototype",
                "id": prototype_name,
                "name": prototype_name,
                "root_path": None,
                "source": "scope-fallback",
            }
        return {
            "type": "game-prototype",
            "id": prototype_name,
            "name": prototype_name,
            "root_path": f"games/{prototype_name}",
            "source": "scope-fallback",
        }

    @staticmethod
    def _board_lifecycle_zh(lifecycle: str) -> str:
        return {
            "ACTIVE": "进行中",
            "REVIEW": "评审中",
            "PROMISING": "待选择",
            "SELECTED": "已选定",
            "INTEGRATED": "已集成",
            "REJECTED": "已拒绝",
            "ARCHIVED": "已归档",
        }.get(lifecycle, lifecycle)

    @staticmethod
    def _board_health_zh(health: str) -> str:
        return {
            "PASS": "正常",
            "FAIL": "异常",
            "UNKNOWN": "未知",
        }.get(health, health)

    @staticmethod
    def _board_next_gate_zh(next_gate: str) -> str:
        return {
            "IMPLEMENT_OR_REVIEW": "继续实现 / 进入评审",
            "CANDIDATE_BUILD": "构建候选版本",
            "HUMAN_REVIEW": "人工评审",
            "HUMAN_PROMOTION": "决定是否晋级",
            "HUMAN_DECISION": "人工决策",
            "TRUSTED_REHEARSAL": "可信彩排",
            "HUMAN_SELECTION_OR_REFRESH_REHEARSAL": "人工选择 / 必要时刷新彩排",
            "TRUSTED_INTEGRATION_OR_REFRESH_REHEARSAL": "集成 / 必要时刷新彩排",
            "ARCHIVE_OR_RETAIN": "选择归档方式",
            "ARCHIVE_RECOVERY": "恢复归档",
            "ARCHIVE": "归档",
            "TERMINAL_NEW_EXPERIMENT_FOR_NEW_WORK": "已结束；新工作需新建实验",
            "VERIFY_EXPERIMENT_HEALTH": "核验实验健康",
            "DO_NOT_USE_RECREATE_EXPERIMENT": "禁止继续；重建实验",
            "UNKNOWN": "未知",
        }.get(next_gate, next_gate)

    @staticmethod
    def _board_relationship_type_zh(relation_type: str) -> str:
        return {
            "depends_on": "依赖",
            "blocks": "阻塞",
            "supersedes": "替代",
        }.get(relation_type, relation_type)

    @staticmethod
    def _board_relationship_incoming_zh(relation_type: str) -> str:
        return {
            "depends_on": "被依赖",
            "blocks": "被阻塞",
            "supersedes": "被替代",
        }.get(relation_type, relation_type)

    @classmethod
    def _board_relationships(cls, manifest: dict[str, Any]) -> list[dict[str, str]]:
        raw = manifest.get("relationships")
        if not isinstance(raw, list):
            return []
        rows: list[dict[str, str]] = []
        for relation in raw:
            if not isinstance(relation, dict):
                continue
            relation_type = relation.get("type")
            target = relation.get("experiment_id")
            if not isinstance(relation_type, str) or not isinstance(target, str):
                continue
            rows.append(
                {
                    "type": relation_type,
                    "type_zh": cls._board_relationship_type_zh(relation_type),
                    "experiment_id": target,
                }
            )
        return rows

    @staticmethod
    def _board_attention(
        health: dict[str, str],
        next_gate: str,
    ) -> dict[str, Any]:
        if health["status"] == "FAIL":
            return {
                "required": True,
                "priority": 0,
                "reason": "HEALTH_FAIL",
                "reason_zh": "健康异常",
                "section": "ABNORMAL",
                "section_zh": "异常",
                "action_zh": "禁止继续；先处理健康异常",
            }
        if health["status"] == "UNKNOWN":
            return {
                "required": True,
                "priority": 1,
                "reason": "HEALTH_UNKNOWN",
                "reason_zh": "健康状态未知",
                "section": "ABNORMAL",
                "section_zh": "异常",
                "action_zh": "先核验实验健康",
            }
        if next_gate == "ARCHIVE_RECOVERY":
            return {
                "required": True,
                "priority": 2,
                "reason": "ARCHIVE_RECOVERY",
                "reason_zh": "归档需要恢复",
                "section": "RECOVERY",
                "section_zh": "需要恢复",
                "action_zh": "恢复同一归档操作",
            }
        if next_gate == "HUMAN_REVIEW":
            return {
                "required": True,
                "priority": 3,
                "reason": "HUMAN_REVIEW",
                "reason_zh": "等待人工评审",
                "section": "REVIEW",
                "section_zh": "需要你评审",
                "action_zh": "完成 PASS / FAIL 人工评审",
            }
        if next_gate in {
            "HUMAN_PROMOTION",
            "HUMAN_DECISION",
            "HUMAN_SELECTION_OR_REFRESH_REHEARSAL",
        }:
            return {
                "required": True,
                "priority": 4,
                "reason": next_gate,
                "reason_zh": "等待人工决策",
                "section": "DECISION",
                "section_zh": "需要你决策",
                "action_zh": GameExpClient._board_next_gate_zh(next_gate),
            }
        if next_gate == "ARCHIVE_OR_RETAIN":
            return {
                "required": True,
                "priority": 5,
                "reason": "ARCHIVE_OR_RETAIN",
                "reason_zh": "等待选择归档方式",
                "section": "ARCHIVE_CHOICE",
                "section_zh": "需要选择归档方式",
                "action_zh": "选择保留或删除实验分支",
            }
        return {
            "required": False,
            "priority": None,
            "reason": None,
            "reason_zh": None,
            "section": None,
            "section_zh": None,
            "action_zh": None,
        }

    def _board_activity(
        self,
        *,
        experiment_id: str,
        manifest: dict[str, Any],
        state: dict[str, Any],
        review: dict[str, Any] | None,
        snapshot_head: str,
        paths: list[str],
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []

        def add_event(
            *,
            order: int,
            code: str,
            label_zh: str,
            detail_zh: str | None = None,
            occurred_at: str | None = None,
            source_kind: str,
            source_id: str | None = None,
            github_run_id: str | None = None,
            actor_login: str | None = None,
        ) -> None:
            events.append(
                {
                    "order": order,
                    "code": code,
                    "label_zh": label_zh,
                    "detail_zh": detail_zh,
                    "occurred_at": occurred_at,
                    "source_kind": source_kind,
                    "source_id": source_id,
                    "github_run_id": github_run_id,
                    "actor_login": actor_login,
                }
            )

        created_at = manifest.get("created_at")
        add_event(
            order=0,
            code="EXPERIMENT_CREATED",
            label_zh="创建实验",
            detail_zh=manifest.get("title") if isinstance(manifest.get("title"), str) else None,
            occurred_at=created_at if isinstance(created_at, str) else None,
            source_kind="manifest",
            source_id=experiment_id,
            actor_login=(
                state.get("_initiator_login")
                if isinstance(state.get("_initiator_login"), str)
                else None
            ),
        )

        decision_prefix = f"experiments/{experiment_id}/decisions/"
        decision_paths = sorted(
            path
            for path in paths
            if path.startswith(decision_prefix) and path.endswith(".json")
        )
        decision_rows: list[dict[str, Any]] = []
        for path in decision_paths:
            row = self.transport.ledger_json(path, ref=snapshot_head)
            if isinstance(row, dict):
                decision_rows.append(row)
        decision_rows.sort(
            key=lambda row: (
                row.get("sequence") if isinstance(row.get("sequence"), int) else 999999,
                str(row.get("decision_id") or ""),
            )
        )
        decision_order = {
            "REVIEW": 20,
            "ACTIVE": 35,
            "PROMISING": 60,
            "SELECTED": 80,
            "REJECTED": 90,
        }
        decision_label = {
            "REVIEW": "进入评审",
            "ACTIVE": "返回进行中",
            "PROMISING": "晋级待选择",
            "SELECTED": "已选定候选",
            "REJECTED": "已拒绝",
        }
        for index, row in enumerate(decision_rows):
            to_state = row.get("to_state")
            if not isinstance(to_state, str):
                continue
            from_state = row.get("from_state")
            detail = None
            if isinstance(from_state, str):
                detail = (
                    f"{self._board_lifecycle_zh(from_state)} → "
                    f"{self._board_lifecycle_zh(to_state)}"
                )
            add_event(
                order=decision_order.get(to_state, 40) + index,
                code=f"LIFECYCLE_{to_state}",
                label_zh=decision_label.get(
                    to_state,
                    f"阶段变更为{self._board_lifecycle_zh(to_state)}",
                ),
                detail_zh=detail,
                source_kind="decision",
                source_id=row.get("decision_id") if isinstance(row.get("decision_id"), str) else None,
                actor_login=(
                    row.get("actor", {}).get("login")
                    if isinstance(row.get("actor"), dict)
                    and isinstance(row.get("actor", {}).get("login"), str)
                    else None
                ),
            )

        candidate_id = state.get("current_candidate_id")
        if isinstance(candidate_id, str) and candidate_id:
            candidate = self.transport.ledger_json(
                f"experiments/{experiment_id}/candidates/{candidate_id}.json",
                ref=snapshot_head,
            )
            github_run_id = (
                str(candidate.get("github_run_id"))
                if isinstance(candidate, dict) and candidate.get("github_run_id") is not None
                else None
            )
            add_event(
                order=40,
                code="CANDIDATE_READY",
                label_zh="候选版本已生成",
                detail_zh=candidate_id,
                source_kind="candidate",
                source_id=candidate_id,
                github_run_id=github_run_id,
            )

        if isinstance(review, dict):
            outcome = review.get("outcome")
            review_id = review.get("review_id")
            add_event(
                order=50,
                code="HUMAN_REVIEW_RECORDED",
                label_zh=(
                    "人工评审通过"
                    if outcome == "PASS"
                    else "人工评审未通过"
                    if outcome == "FAIL"
                    else "已记录人工评审"
                ),
                detail_zh=review.get("notes") if isinstance(review.get("notes"), str) else None,
                source_kind="review",
                source_id=review_id if isinstance(review_id, str) else None,
                actor_login=(
                    review.get("actor", {}).get("login")
                    if isinstance(review.get("actor"), dict)
                    and isinstance(review.get("actor", {}).get("login"), str)
                    else None
                ),
            )

        rehearsal_id = state.get("current_rehearsal_id")
        if isinstance(rehearsal_id, str) and rehearsal_id:
            rehearsal = self.transport.ledger_json(
                f"experiments/{experiment_id}/rehearsals/{rehearsal_id}.json",
                ref=snapshot_head,
            )
            github_run_id = (
                str(rehearsal.get("github_run_id"))
                if isinstance(rehearsal, dict) and rehearsal.get("github_run_id") is not None
                else None
            )
            add_event(
                order=70,
                code="REHEARSAL_READY",
                label_zh="可信彩排完成",
                detail_zh=rehearsal_id,
                source_kind="rehearsal",
                source_id=rehearsal_id,
                github_run_id=github_run_id,
            )

        integration_id = state.get("current_integration_id")
        if isinstance(integration_id, str) and integration_id:
            integration = self.transport.ledger_json(
                f"experiments/{experiment_id}/integrations/{integration_id}.json",
                ref=snapshot_head,
            )
            pr = integration.get("pr") if isinstance(integration, dict) else None
            occurred_at = (
                pr.get("merged_at")
                if isinstance(pr, dict) and isinstance(pr.get("merged_at"), str)
                else None
            )
            pr_number = (
                str(pr.get("number"))
                if isinstance(pr, dict) and pr.get("number") is not None
                else None
            )
            github_run_id = (
                str(integration.get("github_run_id"))
                if isinstance(integration, dict) and integration.get("github_run_id") is not None
                else None
            )
            add_event(
                order=100,
                code="INTEGRATED",
                label_zh="已集成",
                detail_zh=f"PR #{pr_number}" if pr_number else integration_id,
                occurred_at=occurred_at,
                source_kind="integration",
                source_id=integration_id,
                github_run_id=github_run_id,
            )

        archive_id = state.get("current_archive_id")
        if isinstance(archive_id, str) and archive_id:
            archive = self.transport.ledger_json(
                f"experiments/{experiment_id}/archives/{archive_id}.json",
                ref=snapshot_head,
            )
            mode = archive.get("mode") if isinstance(archive, dict) else None
            detail = {
                "ATOMIC_DELETE": "已删除实验分支",
                "RETAIN_BRANCH": "已保留实验分支",
            }.get(mode, archive_id)
            add_event(
                order=110,
                code="ARCHIVED",
                label_zh="已归档",
                detail_zh=detail,
                source_kind="archive",
                source_id=archive_id,
            )

        events.sort(key=lambda row: (row["order"], str(row.get("source_id") or "")))
        return events


    def _board_experiment_health(
        self,
        experiment_id: str,
        manifest: dict[str, Any],
        snapshot_head: str,
        *,
        binding: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        if binding is None:
            binding = self.transport.ledger_json(
                f"experiments/{experiment_id}/binding.json",
                ref=snapshot_head,
            )
        if not isinstance(binding, dict):
            return {"status": "UNKNOWN", "code": "BINDING_MISSING"}

        request_id = binding.get("request_id")
        inputs_digest = binding.get("inputs_digest")
        initialization = binding.get("initialization")
        if (
            not isinstance(request_id, str)
            or not request_id
            or not isinstance(inputs_digest, str)
            or not isinstance(initialization, dict)
        ):
            return {"status": "FAIL", "code": "BINDING_RECORD_INVALID"}

        request = self.transport.ledger_json(
            f"operations/{request_id}.json",
            ref=snapshot_head,
        )
        if not isinstance(request, dict):
            return {"status": "UNKNOWN", "code": "BINDING_REQUEST_MISSING"}

        stored_digest = request.get("payload_digest")
        payload = request.get("payload")
        if not isinstance(stored_digest, str) or not isinstance(payload, dict):
            return {"status": "FAIL", "code": "BINDING_REQUEST_INVALID"}

        actual_digest = digest_object(payload)
        if actual_digest != stored_digest:
            return {
                "status": "FAIL",
                "code": "BINDING_REQUEST_PAYLOAD_DIGEST_MISMATCH",
            }
        if inputs_digest != stored_digest:
            return {
                "status": "FAIL",
                "code": "BINDING_INPUTS_DIGEST_MISMATCH",
            }

        manifest_digest = initialization.get("manifest_digest")
        if not isinstance(manifest_digest, str):
            return {"status": "FAIL", "code": "BINDING_MANIFEST_DIGEST_MISSING"}
        if digest_object(manifest) != manifest_digest:
            return {"status": "FAIL", "code": "BINDING_MANIFEST_DIGEST_MISMATCH"}

        return {"status": "PASS", "code": "BINDING_CHAIN_VERIFIED"}

    def board(
        self,
        *,
        query: str | None = None,
        subject_id: str | None = None,
        lifecycle: str | None = None,
        attention_only: bool = False,
        _snapshot_head: str | None = None,
    ) -> dict[str, Any]:
        requested_lifecycle = lifecycle
        try:
            if _snapshot_head is not None:
                if not re.fullmatch(r"[0-9a-f]{40}", _snapshot_head):
                    raise ClientError("Board snapshot head must be a 40-character SHA")
                snapshot_head = _snapshot_head
            else:
                snapshot_head = self.transport.ledger_head()
            paths = self.transport.ledger_paths(snapshot_head)
        except ClientError as exc:
            return {
                "status": "UNKNOWN",
                "repo": self.transport.repo,
                "reason": "board_snapshot_unavailable",
                "error": str(exc),
            }

        experiment_ids = sorted(
            {
                match.group(1)
                for path in paths
                if (
                    match := re.fullmatch(
                        r"experiments/(EXP-[1-9][0-9]*)/state\.json",
                        path,
                    )
                )
            },
            key=lambda value: int(value.removeprefix("EXP-")),
        )

        items: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        health_counts: dict[str, int] = {}
        for experiment_id in experiment_ids:
            state = self.transport.ledger_json(
                f"experiments/{experiment_id}/state.json",
                ref=snapshot_head,
            )
            manifest = self.transport.ledger_json(
                f"experiments/{experiment_id}/manifest.json",
                ref=snapshot_head,
            )
            binding = self.transport.ledger_json(
                f"experiments/{experiment_id}/binding.json",
                ref=snapshot_head,
            )
            if state is None or manifest is None:
                return {
                    "status": "UNKNOWN",
                    "repo": self.transport.repo,
                    "snapshot_head": snapshot_head,
                    "reason": "board_snapshot_incomplete",
                    "experiment_id": experiment_id,
                }

            lifecycle = state.get("lifecycle")
            if not isinstance(lifecycle, str) or not lifecycle:
                return {
                    "status": "UNKNOWN",
                    "repo": self.transport.repo,
                    "snapshot_head": snapshot_head,
                    "reason": "board_state_invalid",
                    "experiment_id": experiment_id,
                }

            health = self._board_experiment_health(
                experiment_id,
                manifest,
                snapshot_head,
                binding=binding if isinstance(binding, dict) else None,
            )

            review_id = state.get("current_review_id")
            review = None
            if isinstance(review_id, str) and review_id:
                review = self.transport.ledger_json(
                    f"experiments/{experiment_id}/reviews/{review_id}.json",
                    ref=snapshot_head,
                )

            exp_meta = manifest.get("experiment")
            issue_number = None
            if isinstance(exp_meta, dict):
                raw_issue = exp_meta.get("issue_number")
                if isinstance(raw_issue, str):
                    issue_number = raw_issue

            next_gate = (
                "DO_NOT_USE_RECREATE_EXPERIMENT"
                if health["status"] == "FAIL"
                else (
                    "VERIFY_EXPERIMENT_HEALTH"
                    if health["status"] == "UNKNOWN"
                    else self._board_next_gate(state, review)
                )
            )
            subject = self._board_subject(manifest)
            attention = self._board_attention(health, next_gate)
            relationships_outgoing = self._board_relationships(manifest)
            activity_state = dict(state)
            initiator = (
                binding.get("initiator")
                if isinstance(binding, dict) and isinstance(binding.get("initiator"), dict)
                else None
            )
            if isinstance(initiator, dict) and isinstance(initiator.get("login"), str):
                activity_state["_initiator_login"] = initiator["login"]
            activity = self._board_activity(
                experiment_id=experiment_id,
                manifest=manifest,
                state=activity_state,
                review=review if isinstance(review, dict) else None,
                snapshot_head=snapshot_head,
                paths=paths,
            )
            display = {
                "lifecycle": self._board_lifecycle_zh(lifecycle),
                "health": self._board_health_zh(health["status"]),
                "next_gate": self._board_next_gate_zh(next_gate),
                "attention_section": attention.get("section_zh"),
                "attention_reason": attention.get("reason_zh"),
                "attention_action": attention.get("action_zh"),
            }
            initialization = (
                binding.get("initialization")
                if isinstance(binding, dict)
                else None
            )
            if not isinstance(initialization, dict):
                initialization = {}

            item = {
                "repository": self.transport.repo,
                "repository_name": self.transport.repo.split("/", 1)[-1],
                "experiment_id": experiment_id,
                "issue_number": issue_number,
                "title": manifest.get("title"),
                "created_at": manifest.get("created_at"),
                "subject": subject,
                "subject_type": subject["type"],
                "subject_id": subject["id"],
                "subject_name": subject["name"],
                "subject_root_path": subject["root_path"],
                "subject_source": subject["source"],
                "prototype_name": subject["name"],
                "hypothesis": manifest.get("hypothesis"),
                "initiator": (
                    binding.get("initiator")
                    if isinstance(binding.get("initiator"), dict)
                    else None
                ),
                "success_criteria": manifest.get("success_criteria"),
                "kill_criteria": manifest.get("kill_criteria"),
                "relationships_outgoing": relationships_outgoing,
                "relationships_incoming": [],
                "activity": activity,
                "latest_activity": activity[-1] if activity else None,
                "lifecycle": lifecycle,
                "display": display,
                "candidate_id": state.get("current_candidate_id"),
                "review_id": review_id,
                "review_outcome": (
                    review.get("outcome") if isinstance(review, dict) else None
                ),
                "rehearsal_id": state.get("current_rehearsal_id"),
                "integration_id": state.get("current_integration_id"),
                "archive_id": state.get("current_archive_id"),
                "archive_lock": state.get("archive_lock"),
                "parent_sha": (
                    binding.get("parent_sha")
                    if isinstance(binding, dict)
                    else None
                ),
                "branch_ref": initialization.get("branch_ref"),
                "base_tag_ref": initialization.get("base_tag_ref"),
                "final_tag_ref": initialization.get("final_tag_ref"),
                "health": health["status"],
                "health_code": health["code"],
                "next_gate": next_gate,
                "attention": attention,
            }
            contributor_info = self._contributors_for_item(item)
            item["contributors"] = contributor_info.get("contributors", [])
            item["contributors_source"] = contributor_info.get("source")
            item["contributors_complete"] = bool(contributor_info.get("complete"))
            items.append(item)
            counts[lifecycle] = counts.get(lifecycle, 0) + 1
            health_counts[health["status"]] = (
                health_counts.get(health["status"], 0) + 1
            )

        by_id = {item["experiment_id"]: item for item in items}
        relationship_edges: list[dict[str, Any]] = []
        for item in items:
            source_id = item["experiment_id"]
            normalized_outgoing: list[dict[str, Any]] = []
            for relation in item["relationships_outgoing"]:
                target_id = relation["experiment_id"]
                target = by_id.get(target_id)
                edge = {
                    "source_experiment_id": source_id,
                    "target_experiment_id": target_id,
                    "type": relation["type"],
                    "type_zh": relation["type_zh"],
                    "source_title": item.get("title"),
                    "target_title": target.get("title") if target else None,
                    "source_subject_name": item.get("subject_name"),
                    "target_subject_name": target.get("subject_name") if target else None,
                }
                relationship_edges.append(edge)
                normalized_outgoing.append(edge)
                if target is not None:
                    target["relationships_incoming"].append(
                        {
                            **edge,
                            "type_zh": self._board_relationship_incoming_zh(
                                relation["type"]
                            ),
                        }
                    )
            item["relationships_outgoing"] = normalized_outgoing

        for item in items:
            dependency_reviews: list[dict[str, Any]] = []
            if item.get("lifecycle") not in {"ARCHIVED", "REJECTED"}:
                for edge in item.get("relationships_outgoing") or []:
                    if edge.get("type") != "depends_on":
                        continue
                    target = by_id.get(edge.get("target_experiment_id"))
                    if not isinstance(target, dict):
                        continue
                    target_lifecycle = target.get("lifecycle")
                    if target_lifecycle not in {"REJECTED", "ARCHIVED"}:
                        continue
                    reason = (
                        "UPSTREAM_REJECTED"
                        if target_lifecycle == "REJECTED"
                        else "UPSTREAM_ARCHIVED"
                    )
                    dependency_reviews.append(
                        {
                            "code": "DEPENDENCY_REVIEW_REQUIRED",
                            "reason": reason,
                            "target_experiment_id": target.get("experiment_id"),
                            "target_title": target.get("title"),
                            "target_lifecycle": target_lifecycle,
                            "target_lifecycle_zh": target.get("display", {}).get("lifecycle"),
                            "target_integration_id": target.get("integration_id"),
                            "target_final_tag_ref": target.get("final_tag_ref"),
                            "blocks_progress": False,
                            "reason_zh": (
                                "依赖实验已拒绝，需要确认当前实验是否仍成立"
                                if target_lifecycle == "REJECTED"
                                else "依赖实验已归档，需要确认依赖的是已集成能力、不可变快照还是持续开发"
                            ),
                        }
                    )
            item["dependency_reviews"] = dependency_reviews
            if dependency_reviews and not item.get("attention", {}).get("required"):
                item["attention"] = {
                    "required": True,
                    "priority": 6,
                    "reason": "DEPENDENCY_REVIEW_REQUIRED",
                    "reason_zh": "实验依赖需要复核",
                    "section": "DEPENDENCY_REVIEW",
                    "section_zh": "依赖需复核",
                    "action_zh": "确认依赖语义后继续；不会自动淘汰当前实验",
                }
                item["display"]["attention_section"] = "依赖需复核"
                item["display"]["attention_reason"] = "实验依赖需要复核"
                item["display"]["attention_action"] = "确认依赖语义后继续；不会自动淘汰当前实验"

        attention_ids = [
            item["experiment_id"]
            for item in sorted(
                items,
                key=lambda row: (
                    (
                        row["attention"]["priority"]
                        if row["attention"]["required"]
                        else 99
                    ),
                    int(row["experiment_id"].removeprefix("EXP-")),
                ),
            )
            if item["attention"]["required"]
        ]
        attention_section_order = [
            ("ABNORMAL", "异常"),
            ("RECOVERY", "需要恢复"),
            ("REVIEW", "需要你评审"),
            ("DECISION", "需要你决策"),
            ("ARCHIVE_CHOICE", "需要选择归档方式"),
            ("DEPENDENCY_REVIEW", "依赖需复核"),
        ]
        attention_sections: list[dict[str, Any]] = []
        for section_code, section_zh in attention_section_order:
            section_ids = [
                experiment_id
                for experiment_id in attention_ids
                if by_id[experiment_id]["attention"].get("section") == section_code
            ]
            if section_ids:
                attention_sections.append(
                    {
                        "section": section_code,
                        "title_zh": section_zh,
                        "count": len(section_ids),
                        "experiment_ids": section_ids,
                    }
                )

        active_ids = [
            item["experiment_id"]
            for item in items
            if item["lifecycle"] not in {"ARCHIVED", "REJECTED"}
        ]
        archive_ids = [
            item["experiment_id"]
            for item in items
            if item["lifecycle"] == "ARCHIVED"
        ]

        groups: dict[str, dict[str, Any]] = {}
        for item in items:
            key = f'{item["subject_type"]}:{item["subject_id"]}'
            group = groups.setdefault(
                key,
                {
                    "subject": item["subject"],
                    "experiment_ids": [],
                    "counts_by_lifecycle": {},
                    "counts_by_health": {},
                    "attention_count": 0,
                    "relationship_count": 0,
                    "recent_activity": [],
                },
            )
            group["experiment_ids"].append(item["experiment_id"])
            lifecycle_counts = group["counts_by_lifecycle"]
            lifecycle_counts[item["lifecycle"]] = (
                lifecycle_counts.get(item["lifecycle"], 0) + 1
            )
            group_health = group["counts_by_health"]
            group_health[item["health"]] = group_health.get(item["health"], 0) + 1
            if item["attention"]["required"]:
                group["attention_count"] += 1
            group["relationship_count"] += len(item["relationships_outgoing"])
            for event in item["activity"][-3:]:
                group["recent_activity"].append(
                    {
                        **event,
                        "experiment_id": item["experiment_id"],
                        "experiment_title": item.get("title"),
                    }
                )

        prototype_groups = []
        for group in groups.values():
            group["experiment_ids"].sort(
                key=lambda value: int(value.removeprefix("EXP-"))
            )
            group["count"] = len(group["experiment_ids"])
            group["active_count"] = sum(
                1
                for experiment_id in group["experiment_ids"]
                if by_id[experiment_id]["lifecycle"]
                not in {"ARCHIVED", "REJECTED"}
            )
            group["archived_count"] = sum(
                1
                for experiment_id in group["experiment_ids"]
                if by_id[experiment_id]["lifecycle"] == "ARCHIVED"
            )
            latest = max(
                group["experiment_ids"],
                key=lambda value: (
                    str(by_id[value].get("created_at") or ""),
                    int(value.removeprefix("EXP-")),
                ),
            )
            group["latest_experiment_id"] = latest
            group["latest_created_at"] = by_id[latest].get("created_at")
            group["recent_activity"].sort(
                key=lambda row: (
                    str(by_id[row["experiment_id"]].get("created_at") or ""),
                    int(row["experiment_id"].removeprefix("EXP-")),
                    int(row.get("order") or 0),
                ),
                reverse=True,
            )
            group["recent_activity"] = group["recent_activity"][:8]
            group["counts_by_lifecycle"] = dict(
                sorted(group["counts_by_lifecycle"].items())
            )
            group["counts_by_health"] = dict(
                sorted(group["counts_by_health"].items())
            )
            prototype_groups.append(group)

        prototype_groups.sort(
            key=lambda group: (
                0 if group["attention_count"] else 1,
                str(group["subject"].get("name") or ""),
            )
        )

        branch_lanes = [
            {
                "experiment_id": item["experiment_id"],
                "subject_name": item["subject_name"],
                "lifecycle": item["lifecycle"],
                "lifecycle_zh": item["display"]["lifecycle"],
                "title": item.get("title"),
                "parent_sha": item["parent_sha"],
                "branch_ref": item["branch_ref"],
                "base_tag_ref": item["base_tag_ref"],
                "final_tag_ref": item["final_tag_ref"],
                "candidate_id": item["candidate_id"],
                "rehearsal_id": item["rehearsal_id"],
                "integration_id": item["integration_id"],
                "archive_id": item["archive_id"],
            }
            for item in items
        ]

        normalized_query = query.strip().casefold() if isinstance(query, str) else ""
        normalized_subject_id = (
            subject_id.strip() if isinstance(subject_id, str) and subject_id.strip() else None
        )
        normalized_lifecycle = (
            requested_lifecycle.strip().upper()
            if isinstance(requested_lifecycle, str) and requested_lifecycle.strip()
            else None
        )
        focus_ids: list[str] = []
        for item in items:
            if normalized_subject_id and item.get("subject_id") != normalized_subject_id:
                continue
            if normalized_lifecycle and item.get("lifecycle") != normalized_lifecycle:
                continue
            if attention_only and not item.get("attention", {}).get("required"):
                continue
            if normalized_query:
                searchable = " ".join(
                    str(value)
                    for value in (
                        item.get("experiment_id"),
                        item.get("issue_number"),
                        item.get("title"),
                        item.get("subject_name"),
                        item.get("hypothesis"),
                    )
                    if value is not None
                ).casefold()
                if normalized_query not in searchable:
                    continue
            focus_ids.append(item["experiment_id"])

        focus_active = any(
            (
                normalized_query,
                normalized_subject_id,
                normalized_lifecycle,
                attention_only,
            )
        )
        focus_items = [by_id[experiment_id] for experiment_id in focus_ids]
        focus_counts_by_lifecycle: dict[str, int] = {}
        focus_counts_by_health: dict[str, int] = {}
        focus_attention_count = 0
        for item in focus_items:
            item_lifecycle = item["lifecycle"]
            item_health = item["health"]
            focus_counts_by_lifecycle[item_lifecycle] = (
                focus_counts_by_lifecycle.get(item_lifecycle, 0) + 1
            )
            focus_counts_by_health[item_health] = (
                focus_counts_by_health.get(item_health, 0) + 1
            )
            if item.get("attention", {}).get("required"):
                focus_attention_count += 1

        focus = {
            "active": focus_active,
            "query": query.strip() if isinstance(query, str) and query.strip() else None,
            "subject_id": normalized_subject_id,
            "lifecycle": normalized_lifecycle,
            "attention_only": attention_only,
            "count": len(focus_ids),
            "attention_count": focus_attention_count,
            "counts_by_lifecycle": dict(sorted(focus_counts_by_lifecycle.items())),
            "counts_by_health": dict(sorted(focus_counts_by_health.items())),
            "experiment_ids": focus_ids,
            "summary_zh": (
                f"已聚焦 {len(focus_ids)} 个实验，其中 {focus_attention_count} 个需要处理"
                if focus_active
                else f"全部 {len(items)} 个实验，其中 {focus_attention_count} 个需要处理"
            ),
        }

        return {
            "status": "PASS",
            "repo": self.transport.repo,
            "repository_name": self.transport.repo.split("/", 1)[-1],
            "snapshot_head": snapshot_head,
            "count": len(items),
            "attention_count": len(attention_ids),
            "counts_by_lifecycle": dict(sorted(counts.items())),
            "counts_by_health": dict(sorted(health_counts.items())),
            "focus": focus,
            "experiments": items,
            "views": {
                "overview": {
                    "attention_ids": attention_ids,
                    "active_ids": active_ids,
                    "archived_count": len(archive_ids),
                },
                "attention": {
                    "experiment_ids": attention_ids,
                    "sections": attention_sections,
                },
                "prototypes": {
                    "groups": prototype_groups,
                    "relationship_edges": relationship_edges,
                },
                "branches": {
                    "lanes": branch_lanes,
                },
                "archive": {
                    "experiment_ids": archive_ids,
                },
            },
        }

    def prototype_handoff(self, experiment_id: str) -> dict[str, Any]:
        if not EXPERIMENT_ID_RE.fullmatch(experiment_id):
            return {
                "status": "REJECTED",
                "repo": self.transport.repo,
                "experiment_id": experiment_id,
                "reason": "invalid_experiment_id",
            }
        board = self.board(query=experiment_id)
        if board.get("status") != "PASS":
            return board
        row = next(
            (
                item
                for item in board.get("experiments", [])
                if item.get("experiment_id") == experiment_id
            ),
            None,
        )
        if not isinstance(row, dict):
            return {
                "status": "UNKNOWN",
                "repo": self.transport.repo,
                "snapshot_head": board.get("snapshot_head"),
                "experiment_id": experiment_id,
                "reason": "experiment_not_found_in_ledger",
            }
        snapshot_head = board.get("snapshot_head")
        manifest = self.transport.ledger_json(
            f"experiments/{experiment_id}/manifest.json",
            ref=snapshot_head,
        )
        if not isinstance(manifest, dict):
            return {
                "status": "UNKNOWN",
                "repo": self.transport.repo,
                "snapshot_head": snapshot_head,
                "experiment_id": experiment_id,
                "reason": "manifest_not_found_in_ledger",
            }
        runtime = manifest.get("runtime") if isinstance(manifest.get("runtime"), dict) else {}
        scope = manifest.get("scope") if isinstance(manifest.get("scope"), dict) else {}
        branch_ref = row.get("branch_ref")
        branch_head_sha = None
        if isinstance(branch_ref, str) and branch_ref.startswith("refs/heads/"):
            ref = self.transport.git_ref(branch_ref.removeprefix("refs/"))
            obj = ref.get("object") if isinstance(ref, dict) else None
            if isinstance(obj, dict) and isinstance(obj.get("sha"), str):
                branch_head_sha = obj["sha"]
        handoff_id = f"IMPLEMENT_EXPERIMENT:{experiment_id}:{snapshot_head}"
        return {
            "status": "PASS",
            "repo": self.transport.repo,
            "snapshot_head": snapshot_head,
            "experiment_id": experiment_id,
            "handoff_schema_version": 2,
            "handoff_id": handoff_id,
            "handoff_target": "godot-prototype-studio",
            "handoff_kind": "IMPLEMENT_EXPERIMENT",
            "source": {
                "ledger_snapshot": snapshot_head,
                "branch_ref": branch_ref,
                "branch_head_sha": branch_head_sha,
                "parent_sha": row.get("parent_sha"),
                "subject": row.get("subject"),
            },
            "brief": {
                "title": row.get("title"),
                "hypothesis": row.get("hypothesis"),
                "success_criteria": row.get("success_criteria") or [],
                "kill_criteria": row.get("kill_criteria") or [],
                "scope_allowed": scope.get("allowed") if isinstance(scope.get("allowed"), list) else [],
                "scope_avoid": scope.get("avoid") if isinstance(scope.get("avoid"), list) else [],
                "runtime": runtime,
                "review_protocol": (
                    manifest.get("review", {}).get("protocol")
                    if isinstance(manifest.get("review"), dict)
                    else None
                ),
            },
            "return_contract": {
                "schema_version": 2,
                "required": [
                    "source_sha",
                    "build_identity",
                    "checks",
                    "check_environment",
                    "playable_status",
                    "evidence_scope",
                    "artifacts",
                    "delivery_evidence_if_requested",
                ],
                "build_identity": {
                    "required": ["build_id", "source_sha", "producer", "created_from_handoff_id"],
                    "created_from_handoff_id": handoff_id,
                },
                "checks": {
                    "each_requires": [
                        "name",
                        "status",
                        "source_sha",
                        "environment",
                    ]
                },
                "artifacts": {
                    "each_requires": [
                        "artifact_id",
                        "kind",
                        "location",
                        "digest",
                        "portable",
                    ],
                    "rule": "local-only paths must set portable=false; cross-Harness evidence should use a durable accessible location",
                },
                "evidence_scope": {
                    "must_bind": [
                        "experiment_id",
                        "handoff_id",
                        "source_sha",
                        "build_id",
                    ]
                },
                "note_zh": "Godot Prototype Studio 负责实现、运行验证与所需试玩发布；返回证据必须绑定源码、构建身份和可访问产物，game-exp 再继续 Candidate/Review 生命周期。",
            },
        }

    @staticmethod
    def _encode_notification_cursor(value: dict[str, Any]) -> str:
        raw = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        token = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        return "n1." + token

    @staticmethod
    def _decode_notification_cursor(token: str) -> dict[str, Any]:
        if not isinstance(token, str) or not token.startswith("n1."):
            raise ClientError("invalid notification cursor")
        raw = token[3:]
        raw += "=" * ((4 - len(raw) % 4) % 4)
        try:
            value = json.loads(base64.urlsafe_b64decode(raw.encode("ascii")).decode("utf-8"))
        except Exception as exc:
            raise ClientError(f"invalid notification cursor: {exc}") from exc
        if not isinstance(value, dict) or value.get("v") != 1:
            raise ClientError("unsupported notification cursor")
        return value

    def _notification_rows(
        self,
        board: dict[str, Any],
        *,
        viewer_login: str | None,
    ) -> list[dict[str, Any]]:
        items = board.get("experiments", [])
        resolver = getattr(self.transport, "collaborator_permission", None)
        access_cache: dict[str, bool] = {}

        def has_repo_access(login: str) -> bool:
            if login in access_cache:
                return access_cache[login]
            permission = resolver(login) if callable(resolver) else None
            allowed = permission in {
                "pull",
                "read",
                "triage",
                "push",
                "write",
                "maintain",
                "admin",
            }
            access_cache[login] = allowed
            return allowed
        by_subject: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            sid = item.get("subject_id")
            if isinstance(sid, str) and sid:
                by_subject.setdefault(sid, []).append(item)

        event_types = {
            "EXPERIMENT_CREATED": "experiment.created",
            "LIFECYCLE_REVIEW": "experiment.review_entered",
            "CANDIDATE_READY": "candidate.ready",
            "HUMAN_REVIEW_RECORDED": "review.recorded",
            "LIFECYCLE_PROMISING": "experiment.promising",
            "LIFECYCLE_SELECTED": "experiment.selected",
            "LIFECYCLE_REJECTED": "experiment.rejected",
            "INTEGRATED": "experiment.integrated",
            "ARCHIVED": "experiment.archived",
        }
        notifications: list[dict[str, Any]] = []
        for item in items:
            sid = item.get("subject_id")
            if not isinstance(sid, str):
                continue
            participants: set[str] = set()
            for peer in by_subject.get(sid, []):
                initiator = peer.get("initiator")
                if isinstance(initiator, dict):
                    login = initiator.get("login")
                    if isinstance(login, str) and login:
                        participants.add(login)
                for login in peer.get("contributors") or []:
                    if isinstance(login, str) and login:
                        participants.add(login)

            for event in item.get("activity") or []:
                code = event.get("code")
                if code not in event_types:
                    continue
                actor = event.get("actor_login")
                targets = sorted(
                    login
                    for login in participants
                    if not (isinstance(actor, str) and login == actor)
                    and has_repo_access(login)
                )
                if viewer_login is not None and viewer_login not in targets:
                    continue
                source_id = event.get("source_id")
                event_id = (
                    f'{item.get("experiment_id")}:{code}:'
                    f'{source_id if isinstance(source_id, str) and source_id else event.get("order")}'
                )
                notifications.append(
                    {
                        "event_id": event_id,
                        "event_type": event_types[code],
                        "event_version": 1,
                        "experiment_id": item.get("experiment_id"),
                        "subject_id": sid,
                        "subject_name": item.get("subject_name"),
                        "title": item.get("title"),
                        "event_code": code,
                        "event_label_zh": event.get("label_zh"),
                        "detail_zh": event.get("detail_zh"),
                        "occurred_at": event.get("occurred_at"),
                        "actor_login": actor,
                        "initiator": item.get("initiator"),
                        "targets": targets,
                        "source_snapshot": board.get("snapshot_head"),
                        "_order": int(event.get("order") or 0),
                        "delivery": {
                            "mode": "external_adapter",
                            "dedupe_key": event_id,
                        },
                    }
                )

            for review in item.get("dependency_reviews") or []:
                target_id = review.get("target_experiment_id")
                event_id = (
                    f'{item.get("experiment_id")}:DEPENDENCY_REVIEW_REQUIRED:{target_id}'
                )
                targets = sorted(
                    login for login in participants if has_repo_access(login)
                )
                if viewer_login is not None and viewer_login not in targets:
                    continue
                notifications.append(
                    {
                        "event_id": event_id,
                        "event_type": "dependency.review_required",
                        "event_version": 1,
                        "experiment_id": item.get("experiment_id"),
                        "subject_id": sid,
                        "subject_name": item.get("subject_name"),
                        "title": item.get("title"),
                        "event_code": "DEPENDENCY_REVIEW_REQUIRED",
                        "event_label_zh": "依赖需要复核",
                        "detail_zh": review.get("reason_zh"),
                        "occurred_at": None,
                        "actor_login": None,
                        "initiator": item.get("initiator"),
                        "targets": targets,
                        "source_snapshot": board.get("snapshot_head"),
                        "dependency": review,
                        "_order": 95,
                        "delivery": {
                            "mode": "external_adapter",
                            "dedupe_key": event_id,
                        },
                    }
                )
        return notifications

    @staticmethod
    def _notification_sort_key(row: dict[str, Any]) -> tuple[str, int, int, str]:
        experiment_id = str(row.get("experiment_id") or "EXP-0")
        try:
            experiment_number = int(experiment_id.removeprefix("EXP-"))
        except ValueError:
            experiment_number = 0
        return (
            str(row.get("occurred_at") or ""),
            experiment_number,
            int(row.get("_order") or 0),
            str(row.get("event_id") or ""),
        )

    def notification_feed(
        self,
        *,
        viewer_login: str | None = None,
        subject_id: str | None = None,
        limit: int = 50,
        after: str | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 200:
            return {
                "status": "REJECTED",
                "repo": self.transport.repo,
                "reason": "invalid_limit",
            }
        viewer = viewer_login.strip() if isinstance(viewer_login, str) and viewer_login.strip() else None
        subject = subject_id.strip() if isinstance(subject_id, str) and subject_id.strip() else None

        if viewer is not None:
            resolver = getattr(self.transport, "collaborator_permission", None)
            permission = resolver(viewer) if callable(resolver) else None
            if permission not in {"pull", "read", "triage", "push", "write", "maintain", "admin"}:
                return {
                    "status": "REJECTED",
                    "code": "NOTIFICATION_VIEWER_ACCESS_DENIED",
                    "repo": self.transport.repo,
                    "viewer_login": viewer,
                }
        else:
            permission = None

        offset = 0
        after_snapshot = None
        page_snapshot = None
        if after is not None:
            if cursor is not None:
                return {
                    "status": "REJECTED",
                    "code": "CURSOR_ARGUMENT_CONFLICT",
                    "repo": self.transport.repo,
                }
            try:
                checkpoint = self._decode_notification_cursor(after)
            except ClientError as exc:
                return {
                    "status": "REJECTED",
                    "code": "CURSOR_INVALID",
                    "repo": self.transport.repo,
                    "error": str(exc),
                }
            if checkpoint.get("kind") != "checkpoint":
                return {
                    "status": "REJECTED",
                    "code": "CURSOR_INVALID_KIND",
                    "repo": self.transport.repo,
                }
            if checkpoint.get("viewer_login") != viewer or checkpoint.get("subject_id") != subject:
                return {
                    "status": "REJECTED",
                    "code": "CURSOR_SCOPE_MISMATCH",
                    "repo": self.transport.repo,
                }
            after_snapshot = checkpoint.get("snapshot")
        elif cursor is not None:
            try:
                page = self._decode_notification_cursor(cursor)
            except ClientError as exc:
                return {
                    "status": "REJECTED",
                    "code": "CURSOR_INVALID",
                    "repo": self.transport.repo,
                    "error": str(exc),
                }
            if page.get("kind") != "page":
                return {
                    "status": "REJECTED",
                    "code": "CURSOR_INVALID_KIND",
                    "repo": self.transport.repo,
                }
            if page.get("viewer_login") != viewer or page.get("subject_id") != subject:
                return {
                    "status": "REJECTED",
                    "code": "CURSOR_SCOPE_MISMATCH",
                    "repo": self.transport.repo,
                }
            page_snapshot = page.get("snapshot")
            after_snapshot = page.get("after_snapshot")
            offset = page.get("offset")
            if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
                return {
                    "status": "REJECTED",
                    "code": "CURSOR_INVALID",
                    "repo": self.transport.repo,
                }

        board = self.board(subject_id=subject, _snapshot_head=page_snapshot)
        if board.get("status") != "PASS":
            if page_snapshot is not None:
                return {
                    "status": "REJECTED",
                    "code": "CURSOR_EXPIRED",
                    "repo": self.transport.repo,
                    "cursor_snapshot": page_snapshot,
                }
            return board
        snapshot_head = board.get("snapshot_head")
        rows = self._notification_rows(board, viewer_login=viewer)

        if after_snapshot is not None:
            if not isinstance(after_snapshot, str) or not re.fullmatch(r"[0-9a-f]{40}", after_snapshot):
                return {
                    "status": "REJECTED",
                    "code": "CURSOR_INVALID",
                    "repo": self.transport.repo,
                }
            old_board = self.board(subject_id=subject, _snapshot_head=after_snapshot)
            if old_board.get("status") != "PASS":
                return {
                    "status": "REJECTED",
                    "code": "CURSOR_EXPIRED",
                    "repo": self.transport.repo,
                    "cursor_snapshot": after_snapshot,
                }
            old_ids = {
                row.get("event_id")
                for row in self._notification_rows(old_board, viewer_login=viewer)
            }
            rows = [row for row in rows if row.get("event_id") not in old_ids]
            rows.sort(key=self._notification_sort_key)
        else:
            rows.sort(key=self._notification_sort_key, reverse=True)

        total_available = len(rows)
        page_rows = rows[offset : offset + limit]
        for row in page_rows:
            row.pop("_order", None)

        next_cursor = None
        next_offset = offset + len(page_rows)
        if next_offset < total_available:
            next_cursor = self._encode_notification_cursor(
                {
                    "v": 1,
                    "kind": "page",
                    "snapshot": snapshot_head,
                    "after_snapshot": after_snapshot,
                    "offset": next_offset,
                    "viewer_login": viewer,
                    "subject_id": subject,
                }
            )
        checkpoint_cursor = self._encode_notification_cursor(
            {
                "v": 1,
                "kind": "checkpoint",
                "snapshot": snapshot_head,
                "viewer_login": viewer,
                "subject_id": subject,
            }
        )
        return {
            "status": "PASS",
            "repo": self.transport.repo,
            "snapshot_head": snapshot_head,
            "viewer_login": viewer,
            "viewer_permission_snapshot": permission,
            "viewer_permission_authoritative_for_future_delivery": False,
            "recipient_access_filtered": True,
            "subject_id": subject,
            "count": len(page_rows),
            "total_available": total_available,
            "notifications": page_rows,
            "next_cursor": next_cursor,
            "checkpoint_cursor": checkpoint_cursor,
            "checkpoint_ready": next_cursor is None,
            "delivery_contract": {
                "source": "ledger-derived",
                "event_schema_version": 1,
                "dedupe_by": "event_id",
                "recipient_access_filter": "current repository collaborator permission",
                "pagination": "cursor",
                "resume": "after checkpoint_cursor",
                "game_exp_sends_messages": False,
                "retention": {
                    "mode": "protected-ledger-history",
                    "expired_cursor": "CURSOR_EXPIRED",
                },
                "rebuild_rule": "events are deterministically rebuilt from one committed protected Ledger snapshot",
                "note_zh": "外部适配器处理全部分页后再保存 checkpoint_cursor；下次用 after 续读。发送失败不改变实验状态。",
            },
        }

    def subject_panel(self, subject_id: str) -> dict[str, Any]:
        normalized_subject_id = subject_id.strip() if isinstance(subject_id, str) else ""
        if not normalized_subject_id:
            return {
                "status": "REJECTED",
                "repo": self.transport.repo,
                "subject_id": subject_id,
                "reason": "invalid_subject_id",
            }

        board = self.board(subject_id=normalized_subject_id)
        if board.get("status") != "PASS":
            return board

        group = next(
            (
                item
                for item in board.get("views", {})
                .get("prototypes", {})
                .get("groups", [])
                if item.get("subject", {}).get("id") == normalized_subject_id
            ),
            None,
        )
        if not isinstance(group, dict):
            return {
                "status": "UNKNOWN",
                "repo": self.transport.repo,
                "snapshot_head": board.get("snapshot_head"),
                "subject_id": normalized_subject_id,
                "reason": "subject_not_found_in_ledger",
            }

        focus_ids = set(board.get("focus", {}).get("experiment_ids", []))
        experiments = [
            {
                "experiment_id": item.get("experiment_id"),
                "issue_number": item.get("issue_number"),
                "title": item.get("title"),
                "lifecycle": item.get("lifecycle"),
                "lifecycle_zh": item.get("display", {}).get("lifecycle"),
                "health": item.get("health"),
                "health_zh": item.get("display", {}).get("health"),
                "next_gate": item.get("next_gate"),
                "next_action_zh": item.get("display", {}).get("next_gate"),
                "attention": item.get("attention"),
                "initiator": item.get("initiator"),
                "contributors": item.get("contributors") or [],
                "contributors_complete": item.get("contributors_complete"),
                "latest_activity": item.get("latest_activity"),
            }
            for item in board.get("experiments", [])
            if item.get("experiment_id") in focus_ids
        ]
        relationship_edges = [
            edge
            for edge in board.get("views", {})
            .get("prototypes", {})
            .get("relationship_edges", [])
            if (
                edge.get("source_experiment_id") in focus_ids
                or edge.get("target_experiment_id") in focus_ids
            )
        ]

        return {
            "status": "PASS",
            "repo": self.transport.repo,
            "snapshot_head": board.get("snapshot_head"),
            "subject_id": normalized_subject_id,
            "subject": group.get("subject"),
            "summary": {
                "count": group.get("count", 0),
                "active_count": group.get("active_count", 0),
                "archived_count": group.get("archived_count", 0),
                "attention_count": group.get("attention_count", 0),
                "relationship_count": group.get("relationship_count", 0),
                "counts_by_lifecycle": group.get("counts_by_lifecycle", {}),
                "counts_by_health": group.get("counts_by_health", {}),
            },
            "recent_activity": group.get("recent_activity", []),
            "experiments": experiments,
            "relationship_edges": relationship_edges,
        }

    def experiment_panel(self, experiment_id: str) -> dict[str, Any]:
        if not EXPERIMENT_ID_RE.fullmatch(experiment_id):
            return {
                "status": "REJECTED",
                "repo": self.transport.repo,
                "experiment_id": experiment_id,
                "reason": "invalid_experiment_id",
            }

        board = self.board(query=experiment_id)
        if board.get("status") != "PASS":
            return board

        row = next(
            (
                item
                for item in board.get("experiments", [])
                if item.get("experiment_id") == experiment_id
            ),
            None,
        )
        if not isinstance(row, dict):
            return {
                "status": "UNKNOWN",
                "repo": self.transport.repo,
                "snapshot_head": board.get("snapshot_head"),
                "experiment_id": experiment_id,
                "reason": "experiment_not_found_in_ledger",
            }

        return {
            "status": "PASS",
            "repo": self.transport.repo,
            "snapshot_head": board.get("snapshot_head"),
            "experiment_id": experiment_id,
            "overview": {
                "issue_number": row.get("issue_number"),
                "title": row.get("title"),
                "subject": row.get("subject"),
                "lifecycle": row.get("lifecycle"),
                "lifecycle_zh": row.get("display", {}).get("lifecycle"),
                "health": row.get("health"),
                "health_zh": row.get("display", {}).get("health"),
                "health_code": row.get("health_code"),
                "next_gate": row.get("next_gate"),
                "next_action_zh": row.get("display", {}).get("next_gate"),
                "attention": row.get("attention"),
                "initiator": row.get("initiator"),
                "contributors": row.get("contributors") or [],
                "contributors_source": row.get("contributors_source"),
                "contributors_complete": row.get("contributors_complete"),
            },
            "judgement": {
                "hypothesis": row.get("hypothesis"),
                "success_criteria": row.get("success_criteria"),
                "kill_criteria": row.get("kill_criteria"),
            },
            "activity": row.get("activity") or [],
            "relationships": {
                "outgoing": row.get("relationships_outgoing") or [],
                "incoming": row.get("relationships_incoming") or [],
            },
            "evidence": {
                "parent_sha": row.get("parent_sha"),
                "branch_ref": row.get("branch_ref"),
                "base_tag_ref": row.get("base_tag_ref"),
                "final_tag_ref": row.get("final_tag_ref"),
                "candidate_id": row.get("candidate_id"),
                "review_id": row.get("review_id"),
                "review_outcome": row.get("review_outcome"),
                "rehearsal_id": row.get("rehearsal_id"),
                "integration_id": row.get("integration_id"),
                "archive_id": row.get("archive_id"),
            },
        }

    @staticmethod
    def _board_next_gate(
        state: dict[str, Any],
        review: dict[str, Any] | None,
    ) -> str:
        if state.get("archive_lock") is not None:
            return "ARCHIVE_RECOVERY"

        lifecycle = state.get("lifecycle")
        if lifecycle == "ACTIVE":
            return "IMPLEMENT_OR_REVIEW"
        if lifecycle == "REVIEW":
            if not state.get("current_candidate_id"):
                return "CANDIDATE_BUILD"
            if not state.get("current_review_id"):
                return "HUMAN_REVIEW"
            if isinstance(review, dict) and review.get("outcome") == "PASS":
                return "HUMAN_PROMOTION"
            return "HUMAN_DECISION"
        if lifecycle == "PROMISING":
            if (
                state.get("current_rehearsal_id")
                and state.get("current_rehearsal_candidate_id")
                == state.get("current_candidate_id")
            ):
                return "HUMAN_SELECTION_OR_REFRESH_REHEARSAL"
            return "TRUSTED_REHEARSAL"
        if lifecycle == "SELECTED":
            return "TRUSTED_INTEGRATION_OR_REFRESH_REHEARSAL"
        if lifecycle == "INTEGRATED":
            return "ARCHIVE_OR_RETAIN"
        if lifecycle == "REJECTED":
            return "ARCHIVE"
        if lifecycle == "ARCHIVED":
            return "TERMINAL_NEW_EXPERIMENT_FOR_NEW_WORK"
        return "UNKNOWN"

    @staticmethod
    def _validate_execution_arguments(
        action: str,
        arguments: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if action not in ASYNC_EXECUTION_ACTIONS:
            raise ClientError(f"unsupported async execution action: {action}")
        values = dict(arguments or {})
        expected = {
            "initialize": set(),
            "candidate_build": set(),
            "rehearse": set(),
            "integrate": set(),
            "integrate_finalize": {"pr_number"},
            "archive": {"mode"},
        }[action]
        if set(values) != expected:
            raise ClientError(
                f"execution arguments mismatch for {action}: "
                f"expected={sorted(expected)} actual={sorted(values)}"
            )
        if action == "integrate_finalize":
            value = values.get("pr_number")
            if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]*", value):
                raise ClientError("pr_number must be a positive integer")
        if action == "archive" and values.get("mode") not in {
            "ATOMIC_DELETE",
            "RETAIN_BRANCH",
        }:
            raise ClientError("archive mode must be ATOMIC_DELETE or RETAIN_BRANCH")
        return values

    @staticmethod
    def _execution_claim_from_record(
        record: dict[str, Any],
    ) -> dict[str, Any] | None:
        payload = record.get("payload")
        if (
            not isinstance(payload, dict)
            or payload.get("kind") != "operation_request"
            or payload.get("operation") != "execution.claim"
        ):
            return None
        value = payload.get("input")
        return value if isinstance(value, dict) else None

    def _execution_claim_matches(
        self,
        record: dict[str, Any],
        *,
        action: str,
        experiment_id: str,
        arguments: dict[str, Any],
    ) -> bool:
        claim = self._execution_claim_from_record(record)
        return bool(
            isinstance(claim, dict)
            and claim.get("action") == action
            and claim.get("experiment_id") == experiment_id
            and claim.get("arguments") == arguments
        )

    def _wait_for_request_commit(
        self,
        request_id: str,
        initial: dict[str, Any],
        *,
        attempts: int = 20,
        interval: float = 1.0,
    ) -> dict[str, Any]:
        result = initial
        if result.get("status") in {"COMMITTED", "CONFLICT", "REJECTED"}:
            return result
        for _ in range(attempts):
            time.sleep(interval)
            result = self.reconcile(request_id)
            if result.get("status") in {"COMMITTED", "CONFLICT", "REJECTED"}:
                return result
        return result

    def _execution_projection_summary(self, experiment_id: str) -> dict[str, Any] | None:
        projection = self.experiment_get(experiment_id)
        if projection.get("status") != "PASS":
            return None
        state = projection.get("state")
        if not isinstance(state, dict):
            return None
        return {
            "lifecycle": state.get("lifecycle"),
            "sequence": state.get("sequence"),
            "last_decision_id": state.get("last_decision_id"),
            "current_candidate_id": state.get("current_candidate_id"),
            "current_review_id": state.get("current_review_id"),
            "current_rehearsal_id": state.get("current_rehearsal_id"),
            "current_integration_id": state.get("current_integration_id"),
            "current_archive_id": state.get("current_archive_id"),
        }

    def _execution_run_result(
        self,
        *,
        request_id: str,
        claim_record: dict[str, Any],
        run: dict[str, Any],
    ) -> dict[str, Any]:
        claim = self._execution_claim_from_record(claim_record) or {}
        experiment_id = claim.get("experiment_id")
        action = claim.get("action")
        status = run.get("status")
        conclusion = run.get("conclusion")
        base = {
            "request_id": request_id,
            "repo": self.transport.repo,
            "action": action,
            "experiment_id": experiment_id,
            "arguments": claim.get("arguments") or {},
            "claim_status": "COMMITTED",
            "workflow": run,
        }
        if status != "completed":
            return {
                "status": "ACCEPTED",
                "operation_status": "RUNNING",
                **base,
            }
        if conclusion == "success":
            return {
                "status": "PASS",
                "operation_status": "SUCCEEDED",
                **base,
                "result_projection": (
                    self._execution_projection_summary(experiment_id)
                    if isinstance(experiment_id, str)
                    else None
                ),
            }
        if conclusion in {"cancelled", "skipped", "neutral"}:
            return {
                "status": "UNKNOWN",
                "operation_status": "NOT_COMPLETED",
                **base,
            }
        return {
            "status": "REJECTED",
            "operation_status": "FAILED",
            **base,
        }

    def operation_get(self, request_id: str) -> dict[str, Any]:
        rid = validate_request_id(request_id)
        record = self.transport.ledger_record(rid)
        if record is None:
            return self.reconcile(rid)
        claim = self._execution_claim_from_record(record)
        if claim is None:
            return self.reconcile(rid)
        action = claim.get("action")
        experiment_id = claim.get("experiment_id")
        if (
            not isinstance(action, str)
            or action not in ASYNC_EXECUTION_ACTIONS
            or not isinstance(experiment_id, str)
        ):
            return {
                "status": "REJECTED",
                "operation_status": "INVALID_CLAIM",
                "request_id": rid,
                "repo": self.transport.repo,
            }
        run = self.transport.find_execution_run(action=action, request_id=rid)
        if run is not None:
            return self._execution_run_result(
                request_id=rid,
                claim_record=record,
                run=run,
            )
        return {
            "status": "ACCEPTED",
            "operation_status": "CLAIMED",
            "request_id": rid,
            "repo": self.transport.repo,
            "action": action,
            "experiment_id": experiment_id,
            "arguments": claim.get("arguments") or {},
            "claim_status": "COMMITTED",
            "safe_to_resume": True,
        }

    def resume_execution(self, request_id: str) -> dict[str, Any]:
        rid = validate_request_id(request_id)
        record = self.transport.ledger_record(rid)
        if record is None:
            return {
                "status": "UNKNOWN",
                "operation_status": "CLAIM_NOT_FOUND",
                "request_id": rid,
                "repo": self.transport.repo,
                "reason": "execution_claim_not_committed",
            }
        claim = self._execution_claim_from_record(record)
        if claim is None:
            return {
                "status": "REJECTED",
                "operation_status": "NOT_ASYNC_EXECUTION",
                "request_id": rid,
                "repo": self.transport.repo,
            }
        action = claim.get("action")
        experiment_id = claim.get("experiment_id")
        arguments = claim.get("arguments")
        state_digest = claim.get("state_digest")
        if (
            not isinstance(action, str)
            or action not in ASYNC_EXECUTION_ACTIONS
            or not isinstance(experiment_id, str)
            or not isinstance(arguments, dict)
            or not isinstance(state_digest, str)
        ):
            return {
                "status": "REJECTED",
                "operation_status": "INVALID_CLAIM",
                "request_id": rid,
                "repo": self.transport.repo,
            }

        run = self.transport.find_execution_run(action=action, request_id=rid)
        if run is not None:
            return self._execution_run_result(
                request_id=rid,
                claim_record=record,
                run=run,
            )

        snapshot_head = self.transport.ledger_head()
        state = self.transport.ledger_json(
            f"experiments/{experiment_id}/state.json",
            ref=snapshot_head,
        )
        if not isinstance(state, dict):
            return {
                "status": "UNKNOWN",
                "operation_status": "PRECONDITION_UNAVAILABLE",
                "request_id": rid,
                "repo": self.transport.repo,
                "experiment_id": experiment_id,
            }
        if digest_object(state) != state_digest:
            return {
                "status": "CONFLICT",
                "conflict_type": "EXECUTION_PRECONDITION_CHANGED",
                "operation_status": "STALE",
                "request_id": rid,
                "repo": self.transport.repo,
                "experiment_id": experiment_id,
                "action": action,
                "expected_state_digest": state_digest,
                "actual_state_digest": digest_object(state),
            }
        try:
            workflow_url = self.transport.dispatch_execution(
                action=action,
                experiment_id=experiment_id,
                request_id=rid,
                arguments=arguments,
            )
        except TransportUncertainError as exc:
            return {
                "status": "UNKNOWN",
                "operation_status": "DISPATCH_UNCERTAIN",
                "request_id": rid,
                "repo": self.transport.repo,
                "experiment_id": experiment_id,
                "action": action,
                "error": str(exc),
                "recovery": "query the same request_id; do not create a new one",
            }
        return {
            "status": "ACCEPTED",
            "operation_status": "DISPATCHED",
            "request_id": rid,
            "repo": self.transport.repo,
            "experiment_id": experiment_id,
            "action": action,
            "arguments": arguments,
            "workflow_url": workflow_url,
        }

    def start_execution(
        self,
        *,
        action: str,
        experiment_id: str,
        request_id: str | None,
        arguments: dict[str, Any] | None = None,
        actor_claim: str | None = None,
    ) -> dict[str, Any]:
        if request_id is None:
            return {
                "status": "REJECTED",
                "repo": self.transport.repo,
                "experiment_id": experiment_id,
                "action": action,
                "error": "stable request_id is required for mutating async operations",
            }
        rid = validate_request_id(request_id)
        if not EXPERIMENT_ID_RE.fullmatch(experiment_id):
            return {
                "status": "REJECTED",
                "repo": self.transport.repo,
                "request_id": rid,
                "experiment_id": experiment_id,
                "error": "experiment_id must be EXP-<positive integer>",
            }
        try:
            values = self._validate_execution_arguments(action, arguments)
        except ClientError as exc:
            return {
                "status": "REJECTED",
                "repo": self.transport.repo,
                "request_id": rid,
                "experiment_id": experiment_id,
                "action": action,
                "error": str(exc),
            }

        existing = self.transport.ledger_record(rid)
        if existing is not None:
            if not self._execution_claim_matches(
                existing,
                action=action,
                experiment_id=experiment_id,
                arguments=values,
            ):
                return {
                    "status": "CONFLICT",
                    "conflict_type": "REQUEST_ID_CONFLICT",
                    "request_id": rid,
                    "repo": self.transport.repo,
                }
            return self.resume_execution(rid)

        snapshot_head = self.transport.ledger_head()
        state = self.transport.ledger_json(
            f"experiments/{experiment_id}/state.json",
            ref=snapshot_head,
        )
        if not isinstance(state, dict):
            return {
                "status": "REJECTED",
                "repo": self.transport.repo,
                "request_id": rid,
                "experiment_id": experiment_id,
                "error": "experiment is not bound in the authoritative Ledger",
            }
        claim = self.submit(
            operation="execution.claim",
            input_value={
                "experiment_id": experiment_id,
                "action": action,
                "arguments": values,
                "state_digest": digest_object(state),
            },
            request_id=rid,
        )
        claim = self._wait_for_request_commit(rid, claim)
        if claim.get("status") != "COMMITTED":
            return {
                **claim,
                "operation_status": "CLAIM_PENDING",
                "action": action,
                "experiment_id": experiment_id,
                "recovery": "query the same request_id; do not create a new one",
            }
        return self.resume_execution(rid)

    def candidate(
        self,
        experiment_id: str,
        *,
        request_id: str | None = None,
        actor_claim: str | None = None,
    ) -> dict[str, Any]:
        return self.start_execution(
            action="candidate_build",
            experiment_id=experiment_id,
            request_id=request_id,
            actor_claim=actor_claim,
        )

    def initialize(
        self,
        experiment_id: str,
        *,
        request_id: str | None = None,
        actor_claim: str | None = None,
    ) -> dict[str, Any]:
        return self.start_execution(
            action="initialize",
            experiment_id=experiment_id,
            request_id=request_id,
            actor_claim=actor_claim,
        )

    def rehearse(
        self,
        experiment_id: str,
        *,
        request_id: str | None = None,
        actor_claim: str | None = None,
    ) -> dict[str, Any]:
        return self.start_execution(
            action="rehearse",
            experiment_id=experiment_id,
            request_id=request_id,
            actor_claim=actor_claim,
        )

    def integrate(
        self,
        experiment_id: str,
        *,
        request_id: str | None = None,
        actor_claim: str | None = None,
    ) -> dict[str, Any]:
        return self.start_execution(
            action="integrate",
            experiment_id=experiment_id,
            request_id=request_id,
            actor_claim=actor_claim,
        )

    def integrate_finalize(
        self,
        experiment_id: str,
        pr_number: str,
        *,
        request_id: str | None = None,
        actor_claim: str | None = None,
    ) -> dict[str, Any]:
        return self.start_execution(
            action="integrate_finalize",
            experiment_id=experiment_id,
            request_id=request_id,
            arguments={"pr_number": str(pr_number)},
            actor_claim=actor_claim,
        )

    def archive(
        self,
        experiment_id: str,
        mode: str,
        *,
        request_id: str | None = None,
        actor_claim: str | None = None,
    ) -> dict[str, Any]:
        return self.start_execution(
            action="archive",
            experiment_id=experiment_id,
            request_id=request_id,
            arguments={"mode": mode},
            actor_claim=actor_claim,
        )

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
            request_id=request_id,
        )

    def reconcile(self, request_id: str) -> dict[str, Any]:
        rid = validate_request_id(request_id)
        journal = self._read_journal(rid)
        expected_digest = journal.get("payload_digest") if journal else None
        expected_head = journal.get("expected_head") if journal else None
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

        runs = self._discover_request_runs(rid)
        if runs:
            first = runs[0]
            result = self._request_run_projection(
                rid,
                first,
                expected_digest=expected_digest,
                expected_head=expected_head,
            )
            conflicting_attempts = [
                {
                    "databaseId": row.get("databaseId"),
                    "payloadDigest": row.get("payloadDigest"),
                    "expectedHead": row.get("expectedHead"),
                }
                for row in runs[1:]
                if (
                    row.get("payloadDigest") != first.get("payloadDigest")
                    or row.get("expectedHead") != first.get("expectedHead")
                )
            ]
            if conflicting_attempts:
                result["conflicting_attempts"] = conflicting_attempts
            if journal:
                self._write_journal({**journal, **result})
            return result

        workflow_url = journal.get("workflow_url") if journal else None
        if workflow_url:
            state = self.transport.run_state(workflow_url)
            if state and state.get("status") in {
                "queued",
                "in_progress",
                "waiting",
                "requested",
            }:
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
                    conflict_type = (
                        match.group(1)
                        if match and match.group(1).endswith("CONFLICT")
                        else None
                    )
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
            "expected_head": expected_head,
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
                state = self.transport.ledger_json(
                    f"experiments/{experiment_id}/state.json"
                )
                if state is None:
                    add(
                        "archive_health",
                        "SKIP",
                        {
                            "code": "EXPERIMENT_NOT_BOUND",
                            "experiment_id": experiment_id,
                        },
                    )
                else:
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
