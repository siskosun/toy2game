from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import subprocess
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
    "game-exp-github-bridge.yml",
)

PRODUCTION_TOOLS = (
    "archive_control.py",
    "archive_runner.py",
    "bootstrap.py",
    "cli.py",
    "client.py",
    "conformance_core.py",
    "domain_core.py",
    "integration_control.py",
    "github_bridge.py",
    "install_harnesses.py",
    "mcp_server.py",
    "project_policy.py",
    "project_setup.py",
    "protocol_core.py",
    "request_guard.py",
    "rehearsal_control.py",
    "requirements-mcp.txt",
    "source_initializer.py",
    "trusted_writer.py",
    "workflow_guard.py",
)

PLUGIN_FILES = (
    "plugins/game-exp/skills/game-exp/SKILL.md",
    "plugins/game-exp/skills/game-exp/agents/openai.yaml",
    "plugins/game-exp/skills/game-exp/assets/icon.svg",
    "plugins/game-exp/skills/game-exp/references/workflow.md",
    "plugins/game-exp/skills/game-exp/references/board.md",
    "plugins/game-exp/skills/game-exp/references/github-bridge.md",
    "plugins/game-exp/skills/game-exp/references/chat-ui.md",
    "plugins/game-exp/skills/game-exp/references/onboarding.md",
    "plugins/game-exp/skills/game-exp/references/notifications.md",
    "plugins/game-exp/skills/game-exp/references/prototype-handoff.md",
    "plugins/game-exp/skills/game-exp/references/exploration-thread.md",
    "plugins/game-exp/skills/game-exp/references/public-contract.md",
    "plugins/game-exp/skills/game-exp/references/project-setup.md",
    "plugins/game-exp/skills/game-exp/references/conformance.md",
)

def node_npm_policy(node_version: str) -> dict[str, object]:
    return {
        "schema_version": 2,
        "adapter": "node-npm",
        "toolchain": {"node_version": node_version},
        "install": {"argv": ["npm", "ci"]},
        "test": {"argv": ["npm", "test"]},
        "build": {"argv": ["npm", "run", "build"]},
        "candidate": {
            "include": ["dist"],
            "required_paths": ["dist/index.html"],
        },
    }


# Backward-compatible export for tests/importers. Bootstrap generation resolves
# the repository's actual Node version instead of blindly using this default.
NODE_NPM_POLICY = node_npm_policy("22.21.1")

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

    def _node_version(self) -> str | None:
        exact_re = re.compile(
            r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$"
        )

        version_file = self.target_root / ".node-version"
        if version_file.is_file():
            value = version_file.read_text(encoding="utf-8").strip().removeprefix("v")
            if exact_re.fullmatch(value):
                return value
            raise BootstrapError(
                ".node-version must contain an exact Node version such as 22.21.1"
            )

        package_path = self.target_root / "package.json"
        node_spec = None
        if package_path.is_file():
            try:
                package = json.loads(package_path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise BootstrapError(f"invalid package.json: {exc}") from exc
            engines = package.get("engines") if isinstance(package, dict) else None
            raw_node_spec = engines.get("node") if isinstance(engines, dict) else None
            if isinstance(raw_node_spec, str) and raw_node_spec.strip():
                node_spec = raw_node_spec.strip().removeprefix("v")
                if exact_re.fullmatch(node_spec):
                    return node_spec

        node = shutil.which("node")
        if node:
            proc = subprocess.run(
                [node, "--version"],
                text=True,
                encoding="utf-8",
                errors="strict",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if proc.returncode == 0:
                value = proc.stdout.strip().removeprefix("v")
                if exact_re.fullmatch(value):
                    return value

        if node_spec:
            raise BootstrapError(
                "package.json engines.node is not an exact version and local Node "
                "could not provide one; add .node-version with an exact version"
            )
        return None

    def _inferred_policy(self) -> dict[str, object]:
        package = self.target_root / "package.json"
        lock = self.target_root / "package-lock.json"
        shrinkwrap = self.target_root / "npm-shrinkwrap.json"
        if package.is_file() and (lock.is_file() or shrinkwrap.is_file()):
            node_version = self._node_version()
            if not node_version:
                raise BootstrapError(
                    "node-npm project detected but Node version could not be resolved; "
                    "add .node-version or package.json engines.node"
                )
            return node_npm_policy(node_version)

        raise BootstrapError(
            "cannot infer a trusted project policy for this repository; "
            "add .game-exp/project-policy.json using schema v2. "
            "Node/npm auto-detection requires package.json plus package-lock.json "
            "or npm-shrinkwrap.json."
        )

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
            return None

        inferred = self._inferred_policy()
        validate_policy(inferred)
        return PlannedWrite(path, _json_bytes(inferred), "generated")

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
