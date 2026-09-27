from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import sys
import tempfile
import tomllib
import uuid
from typing import Any

from bootstrap import _managed_paths


class HarnessInstallError(RuntimeError):
    pass


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _atomic_write(path: pathlib.Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    tmp = pathlib.Path(raw)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _atomic_replace_dir(source: pathlib.Path, target: pathlib.Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = target.with_name(target.name + ".backup-" + uuid.uuid4().hex)
    had_target = target.exists()
    try:
        if had_target:
            target.rename(backup)
        source.rename(target)
    except Exception:
        if not target.exists() and backup.exists():
            backup.rename(target)
        raise
    else:
        if backup.exists():
            shutil.rmtree(backup)


def _copy_tree_atomic(source: pathlib.Path, target: pathlib.Path) -> None:
    stage = target.with_name(target.name + ".stage-" + uuid.uuid4().hex)
    try:
        shutil.copytree(source, stage)
        _atomic_replace_dir(stage, target)
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def _remove_toml_table(text: str, table_prefix: str) -> str:
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    skip = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            header = stripped.strip("[]").strip()
            skip = header == table_prefix or header.startswith(table_prefix + ".")
        if not skip:
            out.append(line)
    return "".join(out).rstrip() + ("\n" if out else "")


class HarnessInstaller:
    def __init__(
        self,
        source_root: pathlib.Path,
        home: pathlib.Path,
        *,
        runtime_dir: pathlib.Path | None = None,
    ):
        self.source_root = source_root.resolve()
        self.home = home.resolve()
        self.runtime_dir = (
            runtime_dir.resolve()
            if runtime_dir is not None
            else self.home / ".agents" / "tools" / "game-exp"
        )
        self.plugin_path = self.source_root / "plugins" / "game-exp" / "plugin.json"
        self.skill_source = self.source_root / "plugins" / "game-exp" / "skills" / "game-exp"
        self.version = self._load_version()

    def _load_version(self) -> str:
        try:
            value = json.loads(self.plugin_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise HarnessInstallError(f"invalid source plugin.json: {exc}") from exc
        version = value.get("version") if isinstance(value, dict) else None
        if not isinstance(version, str) or not version:
            raise HarnessInstallError("source plugin.json is missing version")
        return version

    def _validate_source(self) -> None:
        missing = [
            rel
            for rel in _managed_paths()
            if not (self.source_root / rel).is_file()
        ]
        if missing:
            raise HarnessInstallError(
                "source runtime is incomplete: " + ", ".join(sorted(missing))
            )
        skill = self.skill_source / "SKILL.md"
        icon = self.skill_source / "assets" / "icon.svg"
        if not skill.read_text(encoding="utf-8").startswith("---\n"):
            raise HarnessInstallError("source SKILL.md frontmatter is invalid")
        icon_bytes = icon.read_bytes()
        if len(icon_bytes) < 64 or b"<svg" not in icon_bytes[:512].lower():
            raise HarnessInstallError("source icon.svg is missing or corrupted")

    def _stage_runtime(self) -> pathlib.Path:
        stage = self.runtime_dir.with_name(
            self.runtime_dir.name + ".stage-" + uuid.uuid4().hex
        )
        stage.mkdir(parents=True, exist_ok=False)
        try:
            for rel in _managed_paths():
                src = self.source_root / rel
                dst = stage / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            (stage / "VERSION.txt").write_text(self.version + "\n", encoding="utf-8")
            self._validate_runtime(stage)
            return stage
        except Exception:
            shutil.rmtree(stage, ignore_errors=True)
            raise

    def _validate_runtime(self, root: pathlib.Path) -> None:
        plugin = json.loads(
            (root / "plugins" / "game-exp" / "plugin.json").read_text(encoding="utf-8")
        )
        if plugin.get("version") != self.version:
            raise HarnessInstallError("staged plugin version mismatch")
        if (root / "VERSION.txt").read_text(encoding="utf-8").strip() != self.version:
            raise HarnessInstallError("staged VERSION.txt mismatch")
        if not (root / "tools" / "game-exp" / "mcp_server.py").is_file():
            raise HarnessInstallError("staged MCP server is missing")
        icon = root / "plugins" / "game-exp" / "skills" / "game-exp" / "assets" / "icon.svg"
        raw = icon.read_bytes()
        if len(raw) < 64 or b"<svg" not in raw[:512].lower():
            raise HarnessInstallError("staged icon.svg is corrupted")

    @property
    def mcp_script(self) -> pathlib.Path:
        return self.runtime_dir / "tools" / "game-exp" / "mcp_server.py"

    def _preflight_configs(self) -> None:
        codex = self.home / ".codex" / "config.toml"
        if codex.exists():
            try:
                tomllib.loads(codex.read_text(encoding="utf-8"))
            except Exception as exc:
                raise HarnessInstallError(f"invalid Codex TOML config {codex}: {exc}") from exc

        for path in (
            self.home / ".qoder" / "settings.json",
            self.home / ".cursor" / "mcp.json",
        ):
            if not path.exists():
                continue
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise HarnessInstallError(f"invalid JSON config {path}: {exc}") from exc
            if not isinstance(value, dict):
                raise HarnessInstallError(f"JSON config must be an object: {path}")
            servers = value.get("mcpServers")
            if servers is not None and not isinstance(servers, dict):
                raise HarnessInstallError(f"mcpServers must be an object: {path}")

    def _install_runtime(self) -> None:
        stage = self._stage_runtime()
        old_cwd = pathlib.Path.cwd()
        try:
            # Windows can refuse directory renames when cwd is inside the tree.
            os.chdir(self.home)
            _atomic_replace_dir(stage, self.runtime_dir)
        finally:
            try:
                os.chdir(old_cwd)
            except OSError:
                os.chdir(self.home)
            if stage.exists():
                shutil.rmtree(stage, ignore_errors=True)
        self._validate_runtime(self.runtime_dir)

    def _install_skills(self) -> dict[str, str]:
        source = self.runtime_dir / "plugins" / "game-exp" / "skills" / "game-exp"
        agents_target = self.home / ".agents" / "skills" / "game-exp"
        qoder_target = self.home / ".qoder" / "skills" / "game-exp"
        _copy_tree_atomic(source, agents_target)
        _copy_tree_atomic(source, qoder_target)
        return {
            "shared_agents": str(agents_target),
            "qoder": str(qoder_target),
            "cursor": str(agents_target),
            "codex": str(agents_target),
        }

    def _mcp_json_entry(self) -> dict[str, Any]:
        return {
            "type": "stdio",
            "command": "uv",
            "args": [
                "run",
                "--with",
                "mcp>=2,<3",
                "python",
                str(self.mcp_script),
            ],
        }

    def _install_codex(self) -> pathlib.Path:
        path = self.home / ".codex" / "config.toml"
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        text = _remove_toml_table(text, "mcp_servers.game-exp")
        args = json.dumps(self._mcp_json_entry()["args"], ensure_ascii=False)
        command = json.dumps("uv")
        block = (
            "[mcp_servers.game-exp]\n"
            f"command = {command}\n"
            f"args = {args}\n"
            "enabled = true\n"
        )
        if text and not text.endswith("\n"):
            text += "\n"
        if text.strip():
            text += "\n"
        text += block
        # Fail before replacing the user's config if the merged TOML is invalid.
        tomllib.loads(text)
        _atomic_write(path, text.encode("utf-8"))
        return path

    def _install_json_mcp(self, path: pathlib.Path) -> pathlib.Path:
        if path.exists():
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise HarnessInstallError(f"invalid JSON config {path}: {exc}") from exc
            if not isinstance(value, dict):
                raise HarnessInstallError(f"JSON config must be an object: {path}")
        else:
            value = {}
        servers = value.get("mcpServers")
        if servers is None:
            servers = {}
            value["mcpServers"] = servers
        if not isinstance(servers, dict):
            raise HarnessInstallError(f"mcpServers must be an object: {path}")
        servers["game-exp"] = self._mcp_json_entry()
        encoded = _json_bytes(value)
        json.loads(encoded.decode("utf-8"))
        _atomic_write(path, encoded)
        return path

    def install(self) -> dict[str, Any]:
        self._validate_source()
        if shutil.which("uv") is None:
            raise HarnessInstallError(
                "uv is required for the shared game-exp MCP command but was not found in PATH"
            )
        self._preflight_configs()

        self._install_runtime()
        skills = self._install_skills()
        configs = {
            "codex": str(self._install_codex()),
            "qoder": str(
                self._install_json_mcp(self.home / ".qoder" / "settings.json")
            ),
            "cursor": str(
                self._install_json_mcp(self.home / ".cursor" / "mcp.json")
            ),
        }
        return {
            "status": "PASS",
            "version": self.version,
            "runtime_dir": str(self.runtime_dir),
            "mcp_script": str(self.mcp_script),
            "skills": skills,
            "configs": configs,
            "repo_binding": "dynamic",
            "next_zh": (
                "Codex/Qoder/Cursor 已共享同一 game-exp runtime；"
                "新会话应显式把当前 owner/repo 传给 game_exp_* 工具。"
            ),
        }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Atomically install one game-exp runtime for Codex, Qoder and Cursor"
    )
    parser.add_argument(
        "--source-root",
        default=str(pathlib.Path(__file__).resolve().parents[2]),
    )
    parser.add_argument("--home", default=str(pathlib.Path.home()))
    parser.add_argument("--runtime-dir")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        result = HarnessInstaller(
            pathlib.Path(args.source_root),
            pathlib.Path(args.home),
            runtime_dir=pathlib.Path(args.runtime_dir) if args.runtime_dir else None,
        ).install()
    except (HarnessInstallError, OSError, ValueError, json.JSONDecodeError) as exc:
        result = {"status": "FAIL", "error": str(exc)}
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"FAIL\t{exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"PASS\tgame-exp {result['version']}")
        print(f"runtime\t{result['runtime_dir']}")
        for name, path in result["configs"].items():
            print(f"{name}\t{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
