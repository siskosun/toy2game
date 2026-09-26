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

    def dispatch_candidate(self, experiment_id: str) -> str:
        if not EXPERIMENT_ID_RE.fullmatch(experiment_id):
            raise ClientError("experiment_id must be EXP-<positive integer>")
        proc = _run(
            [
                "gh",
                "workflow",
                "run",
                "game-exp-candidate.yml",
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
                "candidate dispatch did not produce a provable result"
            )
        url = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        if not RUN_URL_RE.search(url):
            raise TransportUncertainError(
                "candidate dispatch returned no run URL; outcome is uncertain"
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

    def board(self) -> dict[str, Any]:
        try:
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
                "lifecycle": lifecycle,
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
                "attention": self._board_attention(health, next_gate),
            }
            items.append(item)
            counts[lifecycle] = counts.get(lifecycle, 0) + 1
            health_counts[health["status"]] = (
                health_counts.get(health["status"], 0) + 1
            )

        by_id = {item["experiment_id"]: item for item in items}
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

        return {
            "status": "PASS",
            "repo": self.transport.repo,
            "repository_name": self.transport.repo.split("/", 1)[-1],
            "snapshot_head": snapshot_head,
            "count": len(items),
            "attention_count": len(attention_ids),
            "counts_by_lifecycle": dict(sorted(counts.items())),
            "counts_by_health": dict(sorted(health_counts.items())),
            "experiments": items,
            "views": {
                "overview": {
                    "attention_ids": attention_ids,
                    "active_ids": active_ids,
                    "archived_count": len(archive_ids),
                },
                "attention": {
                    "experiment_ids": attention_ids,
                },
                "prototypes": {
                    "groups": prototype_groups,
                },
                "branches": {
                    "lanes": branch_lanes,
                },
                "archive": {
                    "experiment_ids": archive_ids,
                },
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

    def candidate(self, experiment_id: str) -> dict[str, Any]:
        if not EXPERIMENT_ID_RE.fullmatch(experiment_id):
            return {
                "status": "REJECTED",
                "repo": self.transport.repo,
                "experiment_id": experiment_id,
                "error": "experiment_id must be EXP-<positive integer>",
            }
        try:
            workflow_url = self.transport.dispatch_candidate(experiment_id)
        except TransportUncertainError as exc:
            return {
                "status": "UNKNOWN",
                "reason": "candidate_dispatch_outcome_uncertain",
                "repo": self.transport.repo,
                "experiment_id": experiment_id,
                "error": str(exc),
                "retry_safe": False,
            }
        return {
            "status": "ACCEPTED",
            "repo": self.transport.repo,
            "experiment_id": experiment_id,
            "workflow_url": workflow_url,
        }

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
