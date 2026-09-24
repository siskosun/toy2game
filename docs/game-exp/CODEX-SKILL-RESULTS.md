# game-exp Codex Skill / Plugin E2E

Validated on: 2026-09-24  
Repository: `siskosun/toy2game`  
Plugin: `game-exp@toy2game-local`  
Plugin version: `0.1.0`  
Codex CLI: `0.155.1`  
Skill/Plugin merge: `12d0127c49ff2a22cfe3b052db52eb33d4a8b7aa`

## Decision

**CODEX_SKILL_PLUGIN_E2E_PASS**

The full trusted domain workflow is now usable from Codex through a repo-local Plugin + Skill over the existing game-exp MCP control plane. The Skill adds orchestration and human-gate behavior only; it introduces no new write authority.

## Packaging and CI

- repo plugin: `plugins/game-exp`
- repo marketplace: `.agents/plugins/marketplace.json`
- project Codex config: `.codex/config.toml`
- Core contract CI: GitHub Actions run `35981875770` — Linux/Windows/macOS PASS
- MCP CI: GitHub Actions run `35981875783` — Linux/Windows/macOS PASS

The Skill contract checks:
- exact domain MCP tool names;
- human-owned Review / PROMISING / SELECTED gates;
- `ACCEPTED` is never treated as completion;
- explicit `ATOMIC_DELETE` / `RETAIN_BRANCH` Archive semantics;
- exactly one Skill entrypoint in the plugin.

## Real Codex installation

On the Windows pilot machine:

1. The repository was marked trusted in the existing user Codex config.
2. Project `.codex/config.toml` loaded the `game-exp` stdio MCP server.
3. The repository marketplace was added as `toy2game-local`.
4. `game-exp@toy2game-local` installed successfully and reported `installed, enabled`.
5. Installed Skill frontmatter was verified in the Codex plugin cache.

The local marketplace/install process exposed one test-environment issue: a PowerShell sync command initially collapsed the Markdown file to one line. The GitHub repository file was valid. The local sync was corrected to preserve UTF-8 line endings, the plugin was reinstalled, and Codex loaded the Skill successfully.

## Natural-language routing E2E

Prompt:

> 检查 game-exp 的 EXP-21 当前状态，告诉我是否还能继续推进，以及下一步是什么。只使用 game-exp 插件/工具，不运行 shell，不直接读取 GitHub。

The prompt did **not** name a specific MCP tool.

Codex selected:

- MCP server: `game-exp`
- tool: `game_exp_experiment_get`
- argument: `experiment_id=EXP-21`

The tool returned the protected-Ledger projection with lifecycle `ARCHIVED`. Codex then answered that EXP-21 cannot continue along the old experiment branch and that any new work should start as a new experiment. It did not invoke any mutation tool.

This validates the intended layering:

```text
Natural-language user intent
        |
        v
game-exp Skill / Plugin
        |
        v
domain-specific game-exp MCP tool
        |
        v
Trusted Client / Workflow / Writer
        |
        v
protected Ledger and protected refs
```

## Observed non-blocking Codex warnings

The pilot Codex installation emitted two unrelated/non-blocking warnings:

- model-catalog refresh timed out and Codex used fallback metadata for the configured `gpt-6-sol`;
- many enabled Skills caused description shortening to fit the Skill context budget.

Neither warning prevented game-exp Skill discovery or MCP tool routing. The second warning suggests disabling unused Skills/Plugins when deterministic trigger competition becomes a problem, but no such routing failure occurred in this E2E.

## UI status

The tested Codex 0.155.1 host still reports MCP Apps rendering feature flags as under-development/disabled. Therefore the production Experiment Board remains deferred.

Current recommended Harness UX is:

1. natural-language game-exp Skill;
2. domain-specific MCP tools;
3. CLI for CI/debug/recovery;
4. future Experiment Board only as a read/projection UI over the same trusted domain APIs once the Codex host provides a stable panel surface.

