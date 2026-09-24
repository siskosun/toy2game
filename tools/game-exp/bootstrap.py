from __future__ import annotations

import argparse
import json
import pathlib
import re
from dataclasses import dataclass
from typing import Iterable

from project_policy import validate_policy

PRODUCTION_WORKFLOWS = (
    "game-exp-trusted-writer.yml",
    "game-exp-trusted-writer-selftest.yml",
    "game-exp-source-initializer.yml",
    "game-exp-candidate.yml",
    "game-exp-rehearsal.yml",
    "game-exp-integration.yml",
    "game-exp-integration-finalize.yml",
    "game-exp-archive.yml",
    "game-exp-archive-snapshot-verify.yml",
)

PRODUCTION_TOOLS = (
    "archive_control.py",
    "archive_runner.py",
    "bootstrap.py",
    "cli.py",
    "client.py",
    "domain_core.py",
    "integration_control.py",
    "mcp_server.py",
    "project_policy.py",
    "protocol_core.py",
    "rehearsal_control.py",
    "requirements-mcp.txt",
    "source_initializer.py",
    "trusted_writer.py",
)

PLUGIN_FILES = (
    "plugins/game-exp/skills/game-exp/SKILL.md",
    "plugins/game-exp/skills/game-exp/references/workflow.md",
    "plugins/game-exp/skills/game-exp/agents/openai.yaml",
)

NODE_NPM_POLICY = {
    "schema_version": 1,
    "adapter": "node-npm",
    "install": {"argv": ["npm", "ci"]},
    "test": {"argv": ["npm", "test"]},
    "build": {"argv": ["npm", "run", "build"]},
    "candidate": {
        "include": ["dist"],
        "required_paths": ["dist/index.html"],
    },
}

REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class BootstrapError(RuntimeError):
    pass


@dataclass(frozen=True)
class PlannedWrite:
    path: pathlib.Path
    content: bytes
    kind: str


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _validate_repo(repo: str) -> str:
    if not REPO_RE.fullmatch(repo):
        raise BootstrapError("repo must be owner/name")
    owner, name = repo.split("/", 1)
    if owner in {".", ".."} or name in {".", ".."}:
        raise BootstrapError("repo must be owner/name")
    return repo


def _managed_paths() -> list[str]:
    paths = [f".github/workflows/{name}" for name in PRODUCTION_WORKFLOWS]
    paths += [f"tools/game-exp/{name}" for name in PRODUCTION_TOOLS]
    paths += list(PLUGIN_FILES)
    paths += ["plugins/game-exp/plugin.json"]
    return paths


class Bootstrapper:
    def __init__(self, source_root: pathlib.Path, target_root: pathlib.Path, repo: str):
        self.source_root = source_root.resolve()
        self.target_root = target_root.resolve()
        self.repo = _validate_repo(repo)

    def _source_bytes(self, rel: str) -> bytes:
        path = self.source_root / rel
        if not path.is_file():
            raise BootstrapError(f"bootstrap source missing: {rel}")
        return path.read_bytes()

    def _copy_writes(self) -> list[PlannedWrite]:
        writes: list[PlannedWrite] = []
        for rel in _managed_paths():
            if rel == "plugins/game-exp/plugin.json":
                try:
                    plugin = json.loads(self._source_bytes(rel).decode("utf-8"))
                except Exception as exc:
                    raise BootstrapError(f"invalid source plugin.json: {exc}") from exc
                if not isinstance(plugin, dict):
                    raise BootstrapError("source plugin.json must be an object")
                plugin["repository"] = f"https://github.com/{self.repo}"
                content = _json_bytes(plugin)
            else:
                content = self._source_bytes(rel)
            writes.append(PlannedWrite(self.target_root / rel, content, "managed"))
        return writes

    def _policy_write(self) -> PlannedWrite | None:
        path = self.target_root / ".game-exp/project-policy.json"
        if path.exists():
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
                validate_policy(current)
            except Exception as exc:
                raise BootstrapError(f"existing project policy is invalid: {exc}") from exc
            if current.get("adapter") != "node-npm":
                raise BootstrapError("bootstrap schema v1 currently supports node-npm only")
            return None
        return PlannedWrite(path, _json_bytes(NODE_NPM_POLICY), "generated")

    def _marketplace_write(self) -> tuple[PlannedWrite, str]:
        path = self.target_root / ".agents/plugins/marketplace.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise BootstrapError(f"invalid marketplace.json: {exc}") from exc
            if not isinstance(data, dict) or not isinstance(data.get("plugins"), list):
                raise BootstrapError("existing marketplace.json must contain a plugins array")
            name = data.get("name")
            if not isinstance(name, str) or not name:
                raise BootstrapError("existing marketplace.json must contain a name")
        else:
            repo_name = self.repo.split("/", 1)[1]
            name = f"{repo_name}-local"
            data = {
                "name": name,
                "interface": {"displayName": f"{repo_name} Plugins"},
                "plugins": [],
            }

        entry = {
            "name": "game-exp",
            "source": {"source": "local", "path": "./plugins/game-exp"},
            "policy": {
                "installation": "INSTALLED_BY_DEFAULT",
                "authentication": "ON_INSTALL",
            },
            "category": "Productivity",
        }
        data["plugins"] = [
            plugin
            for plugin in data["plugins"]
            if not (isinstance(plugin, dict) and plugin.get("name") == "game-exp")
        ]
        data["plugins"].append(entry)
        return PlannedWrite(path, _json_bytes(data), "generated"), name

    def _codex_write(self, marketplace_name: str) -> PlannedWrite:
        path = self.target_root / ".codex/config.toml"
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        mcp_header = "[mcp_servers.game-exp]"
        plugin_header = f'[plugins."game-exp@{marketplace_name}"]'
        existing_plugin = re.search(
            r'(?m)^\[plugins\."game-exp@[^"]+"\]\s*$',
            existing,
        )

        block = (
            f"{mcp_header}\n"
            'command = "uv"\n'
            'args = ["run", "--with", "mcp>=2,<3", "python", '
            '"tools/game-exp/mcp_server.py"]\n'
            'enabled = true\n'
            f'env = {{ GAME_EXP_REPO = "{self.repo}" }}\n\n'
            f"{plugin_header}\n"
            'enabled = true\n'
        )

        if mcp_header in existing or existing_plugin:
            if mcp_header in existing and existing_plugin and block.strip() in existing:
                return PlannedWrite(path, existing.encode("utf-8"), "generated")
            raise BootstrapError(
                "existing .codex/config.toml has a conflicting game-exp configuration"
            )

        prefix = existing
        if prefix and not prefix.endswith("\n"):
            prefix += "\n"
        if prefix:
            prefix += "\n"
        return PlannedWrite(path, (prefix + block).encode("utf-8"), "generated")

    def plan(self) -> list[PlannedWrite]:
        writes = self._copy_writes()
        policy = self._policy_write()
        if policy is not None:
            writes.append(policy)
        marketplace, marketplace_name = self._marketplace_write()
        writes.append(marketplace)
        writes.append(self._codex_write(marketplace_name))
        self._validate_conflicts(writes)
        return writes

    def _validate_conflicts(self, writes: Iterable[PlannedWrite]) -> None:
        if self.source_root == self.target_root:
            raise BootstrapError("source and target repositories must differ")
        if not (self.target_root / ".git").exists():
            raise BootstrapError("target must be a Git repository working tree")

        for item in writes:
            if item.path.exists() and item.kind == "managed":
                if item.path.read_bytes() != item.content:
                    rel = item.path.relative_to(self.target_root).as_posix()
                    raise BootstrapError(f"managed destination already differs: {rel}")

    def install(self) -> list[pathlib.Path]:
        writes = self.plan()
        changed: list[pathlib.Path] = []
        for item in writes:
            if item.path.exists() and item.path.read_bytes() == item.content:
                continue
            item.path.parent.mkdir(parents=True, exist_ok=True)
            item.path.write_bytes(item.content)
            changed.append(item.path)
        return changed


def _source_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install game-exp into another repository")
    parser.add_argument("command", choices=("plan", "install"))
    parser.add_argument("--target", required=True)
    parser.add_argument("--repo", required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    bootstrap = Bootstrapper(_source_root(), pathlib.Path(args.target), args.repo)
    writes = bootstrap.plan()

    if args.command == "plan":
        for item in writes:
            rel = item.path.relative_to(bootstrap.target_root).as_posix()
            unchanged = item.path.exists() and item.path.read_bytes() == item.content
            print(f"{'unchanged' if unchanged else 'write'}\t{rel}")
        return 0

    changed = bootstrap.install()
    for path in changed:
        rel = path.relative_to(bootstrap.target_root).as_posix()
        print(f"installed\t{rel}")
    print(
        "next\tcommit these files, configure Trusted Writer controls, "
        "then run game-exp doctor"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
