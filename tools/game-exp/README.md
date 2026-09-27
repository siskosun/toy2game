# game-exp Phase 2 request core

This directory contains the minimal trusted request client built on the frozen V1.3 protocol.

## Trust boundary

The CLI never writes `game-exp/ledger` directly.

```text
MCP / CLI / GitHub Bridge
    ↓
versioned command/query contract
    ↓
Trusted Writer + execution.claim
    ↓
trusted lifecycle worker
    ↓
protected game-exp/ledger
```

GitHub Rulesets and the dedicated Deploy Key remain the write authority.

## Result states

- `ACCEPTED`: GitHub accepted a Trusted Writer workflow run. This is not yet a Ledger commit.
- `COMMITTED`: the remote Ledger contains the matching request record.
- `CONFLICT`: the request ID or expected Ledger head conflicts with remote state.
- `UNKNOWN`: the client cannot prove whether the remote mutation happened. Reconcile or retry with the same request ID.
- `REJECTED`: local validation or a proven remote rejection failed the request.

A network timeout during dispatch is `UNKNOWN`, not `REJECTED`.

## Commands

From the repository root:

```powershell
python tools/game-exp/cli.py --repo siskosun/toy2game --json status
python tools/game-exp/cli.py --repo siskosun/toy2game --json doctor
python tools/game-exp/cli.py --repo siskosun/toy2game --json capabilities
```

Submit a request:

```powershell
@'
{
  "hypothesis": "three roles improve readability"
}
'@ | Set-Content -Encoding utf8 $env:TEMP\game-exp-input.json

python tools/game-exp/cli.py --repo siskosun/toy2game --json request experiment.create `
  --request-id req-example-1 `
  --input-file $env:TEMP\game-exp-input.json
```

The command returns a stable `request_id`. If the workflow result is uncertain:

```powershell
python tools/game-exp/cli.py --repo siskosun/toy2game --json get-operation <request_id>
```

Re-running `request` with the same request ID and identical payload reuses the original `expected_head`. A different payload with the same request ID is rejected locally before dispatch.

For PowerShell, prefer `--input-file` over inline JSON to avoid shell quoting changes.

## Local journal

The CLI keeps a non-authoritative recovery journal under the Git common directory:

```text
.git/game-exp/requests/
```

It exists only to remember the original payload digest, expected Ledger head and workflow URL. The remote Ledger remains authoritative.

## Project validation policy

Repository-specific build assumptions are defined in `.game-exp/project-policy.json`, not hard-coded into Candidate, Rehearsal, or archived-snapshot workflows.

Schema v1 remains compatible for legacy `node-npm` repositories. Schema v2 is recommended and declares:

- a normalized adapter id;
- adapter toolchain metadata;
- install/test/build commands as argv arrays (no shell command strings);
- Candidate paths to package;
- Candidate paths that must exist in the trusted archive.

For `node-npm`, schema v2 requires an exact `toolchain.node_version`. Candidate and Rehearsal use that value with `actions/setup-node`, so the build no longer depends on a separate `.node-version` file. Other schema-v2 adapters receive no implicit runtime installation; their argv commands must be self-contained on the trusted Ubuntu runner.

The trusted workflows load this policy from the immutable `github.workflow_sha`. Experiment branches cannot alter the policy used to validate themselves. The Candidate receipt records the policy digest, so changing project validation rules changes Candidate identity evidence.

Bootstrap auto-generates a Node/npm policy only for a locked Node project (`package.json` plus `package-lock.json` or `npm-shrinkwrap.json`). For an unknown clean repository type, bootstrap fails and requires an explicit valid policy instead of guessing Node/npm.

## Bootstrap into another repository

Run the installer from a checkout that already contains game-exp:

```powershell
python tools/game-exp/bootstrap.py plan --target C:\path\to\target-repo --repo owner/name
python tools/game-exp/bootstrap.py install --target C:\path\to\target-repo --repo owner/name
```

The bootstrap copies the production game-exp tools, lifecycle workflows, Skill/Plugin files, and generates/merges:

- `.game-exp/project-policy.json` only when a locked Node/npm project can be inferred; otherwise an explicit valid policy must already exist;
- `.agents/plugins/marketplace.json`;
- `.codex/config.toml` with the target `GAME_EXP_REPO`.

It is fail-closed: a different existing managed game-exp file or conflicting Codex game-exp section stops the install before writes. Existing unrelated marketplace plugins are preserved, and an existing valid project policy is preserved.

The bootstrap deliberately does not create or upload the Trusted Writer private key, repository secret, Rulesets, or Immutable Releases settings. After committing the generated files, configure those repository controls and run:

```powershell
python tools/game-exp/cli.py --repo owner/name --json doctor
```

Do not treat bootstrap completion as trust readiness; `doctor` is the verification gate.

## Tests

```bash
python -m unittest discover -s tools/game-exp/tests -v
```

The repository also contains a three-platform GitHub Actions workflow named `game-exp core tests`.

## ChatGPT Web without Developer Mode: GitHub Bridge

When a ChatGPT workspace member cannot enable custom MCP Apps, use the repository GitHub connector plus the trusted Issue-comment bridge instead of exposing a local MCP server.

Bridge command shape:

~~~text
/game-exp
{"schema_version":1,"request_id":"req_example","action":"rehearse","experiment_id":"EXP-42"}
~~~

The command must be posted on Issue #42 for `EXP-42`. The bridge workflow independently checks the comment author and repository write permission, posts a request-scoped claim marker, and then routes the action to the same Trusted Writer / lifecycle workflows. A matching result marker is posted after execution.

Supported actions: `bind`, `initialize`, `candidate_build`, `review_record`, `decision_submit`, `rehearse`, `integrate`, `integrate_finalize`, `archive`, `archive_abort`, and read-only `status`.

If a claim exists without a result, treat the request as `UNKNOWN`; inspect the referenced bridge Actions run and the protected Ledger before attempting any retry. Never use a new request id merely to escape uncertainty.

The bridge does not grant source-editing authority. ChatGPT should use its normal authorized GitHub connector for experiment branch edits, PR creation when the trusted Integration workflow cannot open the PR, and explicit user-authorized PR merge actions.

## One local runtime for Codex, Qoder and Cursor

v0.16.3 uses one local installer so the three Harnesses do not drift onto
different game-exp copies. On Windows, if a live Harness keeps the managed
runtime/Skill directory open, the installer first falls back from directory
swap to per-file atomic replacement. If a specific file also denies
delete-sharing, that file alone is backed up and overwritten in-place with
flush/fsync, then validated before the installation is reported as PASS.

From a trusted game-exp checkout:

```powershell
python tools/game-exp/install_harnesses.py --json
```

The installer:

- stages and validates one runtime under `~/.agents/tools/game-exp`, then swaps
  the directory instead of updating files in place;
- validates `icon.svg`, `SKILL.md`, MCP server presence and plugin version
  before activating the new runtime;
- installs the shared Skill at `~/.agents/skills/game-exp` for Codex/Cursor and
  a Qoder-compatible copy at `~/.qoder/skills/game-exp`;
- merges a global stdio MCP entry into `~/.codex/config.toml`,
  `~/.qoder/settings.json`, and `~/.cursor/mcp.json`;
- preserves unrelated MCP servers/settings;
- preflights existing TOML/JSON before changing the runtime;
- never writes a global `GAME_EXP_REPO`. The shared MCP remains repository
  dynamic, so the Skill must resolve the current GitHub `owner/name` and pass
  `repo` explicitly to `game_exp_*` tools.

The installer requires `uv` on PATH. Re-running it is supported and replaces
the previous managed game-exp runtime/Skill/config entry without duplicating
entries.

After installation, start a new Harness session (or reload MCP/Skills where the
Harness supports it) so the running process picks up the new Skill and MCP
configuration.

## MCP hosts: Codex and ChatGPT Web

The adapter uses the official Python MCP SDK v2 and delegates to the same validated Client / Trusted Domain Core. MCP never gets direct Git authority. The same tool surface supports local stdio for Codex and Streamable HTTP for ChatGPT Web.

Normal Harness use should prefer domain tools:

Read / projection:
- `game_exp_status`
- `game_exp_experiment_template`
- `game_exp_board`
- `game_exp_doctor`
- `game_exp_experiment_get`
- `game_exp_request_get`

Lifecycle / workflow:
- `game_exp_experiment_bind`
- `game_exp_initialize`
- `game_exp_candidate_build`
- `game_exp_review_record`
- `game_exp_decision_submit`
- `game_exp_rehearse`
- `game_exp_integrate`
- `game_exp_integrate_finalize`
- `game_exp_archive`
- `game_exp_archive_abort`

Low-level fallback:
- `game_exp_request_submit`

## Self-describing first experiment

v0.15 removes the need for a new Harness to inspect toy2game or historical Ledger records before creating its first experiment. v0.16 also removes the hidden Node/npm assumption from project-policy generation and publishes project-policy schema v2.

Use:

```powershell
python tools/game-exp/cli.py --json experiment-template
```

or MCP `game_exp_experiment_template`.

The query reads the current repository's `.game-exp/project-policy.json` and returns the recommended Manifest schema, exact project commands/Candidate policy, runtime defaults, review default, required/optional fields, and a non-bindable example blueprint.

New experiments should use Manifest schema v2. Its runtime is project-policy based:

```json
{"adapter":"node-npm","policy_path":".game-exp/project-policy.json"}
```

Schema v1 remains accepted for existing Godot-shaped Manifests.

## Board and Skill

v0.4 adds stable Manifest `subject` identity and a portfolio-style Board:

- `总览`: blockers, human gates, and active work first;
- `待处理`: only experiments requiring attention;
- `原型`: Repository -> Subject/Prototype -> Experiment grouping;
- `分支图`: canonical experiment branch/tag lanes;
- `归档`: terminal experiments separated from active work.

New experiments should bind a stable `subject` (`game-prototype` or `repository`). Legacy experiments without `subject` remain valid and fall back to `scope.allowed` inference for Board grouping.

v0.5 upgrades the Board from a portfolio list to an action-oriented dashboard:

- `待处理` is grouped into Chinese action sections such as `异常`, `需要你评审`, `需要你决策`, and `需要选择归档方式`;
- every experiment exposes a Ledger-derived `activity` timeline with Chinese labels and no fabricated timestamps;
- prototype groups expose recent activity and relationship counts;
- optional `manifest.relationships` models `依赖 / 阻塞 / 替代` while preserving raw machine relation codes for automation;
- all system-generated panel entries use Chinese as the primary UI text.

The repo-local `game-exp` plugin is enabled from `.codex/config.toml` and packages the game-exp Skill. Current plugin version: `0.16.3`.

### Windows UTF-8 compatibility

v0.13.1 pins captured `git` / `gh` subprocess text to strict UTF-8 decoding instead of inheriting the Windows ANSI code page. Invalid UTF-8 fails closed on authoritative/control-plane paths. The CLI configures Windows stdout/stderr as UTF-8 for Chinese status text.

Normal users do not need to remember MCP tool names. Examples:

- `打开 game-exp 面板`
- `继续 EXP-42，告诉我下一步`
- `这个 Candidate 我评审为 PASS`
- `把 EXP-42 晋级到 PROMISING`
- `归档 EXP-42，删除实验分支`

For `打开 game-exp 面板`, the Skill calls `game_exp_board` and renders one consistent protected-Ledger snapshot with:

- repository name and pinned Ledger snapshot;
- experiment id / GitHub Issue;
- inferred game prototype name from `scope.allowed`;
- Chinese lifecycle, health, next-action, relationship, and activity labels;
- action Inbox sections;
- experiment / title / prototype;
- Ledger-derived activity timeline;
- incoming / outgoing experiment relationships;
- branch / Candidate / Review / Rehearsal / Integration / Archive detail on demand.

Board health is a safety gate, not decoration. A failed Binding/request/Manifest digest chain is rendered as `health=FAIL` and `DO_NOT_USE_RECREATE_EXPERIMENT`; the Skill must not recommend normal lifecycle work for that experiment.

The current Codex 0.155.1 host has MCP Apps rendering feature flags disabled/under development. Therefore this is a Harness-native structured/text Board, not a persistent graphical panel. The future graphical Board should reuse the same read-only projection.

Typical Codex flow:

1. Call `game_exp_experiment_get` before changing an existing experiment.
2. Use the domain tool for the next lifecycle action.
3. If a request-style tool returns `ACCEPTED` or `UNKNOWN`, resolve it with `game_exp_request_get`.
4. Re-read `game_exp_experiment_get` after the authoritative mutation lands.
5. Never infer success from a button/tool call alone; the protected Ledger and trusted workflows remain authoritative.

`game_exp_review_record` defaults to the current Candidate when candidate_id is omitted. `game_exp_decision_submit` defaults to the current protected `last_decision_id` when previous_decision_id is omitted, preserving optimistic-concurrency binding without forcing the user to copy IDs manually.

The MCP caller does not authenticate Review or Decision authority. The Trusted Writer independently resolves the GitHub actor and repository permission.

`game_exp_experiment_bind` uses `manifest.operation_id` as its request id. If a request_id is also supplied, it must match exactly.

Install the MCP dependency into an isolated local environment:

```powershell
uv venv .game-exp/mcp-venv
uv pip install --python .game-exp/mcp-venv/Scripts/python.exe -r tools/game-exp/requirements-mcp.txt
```

Register the local stdio server with Codex on Windows:

```powershell
$root = (Get-Location).Path
codex mcp add game-exp --env GAME_EXP_REPO=siskosun/toy2game -- `
  "$root\.game-exp\mcp-venv\Scripts\python.exe" `
  "$root\tools\game-exp\mcp_server.py"
```

Verify:

```powershell
codex mcp list
```

Codex CLI and the IDE extension share MCP configuration. The server uses stdio, so stdout is reserved for the MCP wire.

### ChatGPT Web over Streamable HTTP

Run the same MCP server locally over Streamable HTTP:

```powershell
$env:GAME_EXP_REPO="siskosun/toy2game"
$env:GAME_EXP_MCP_TRANSPORT="streamable-http"
$env:GAME_EXP_MCP_HOST="127.0.0.1"
$env:GAME_EXP_MCP_PORT="8765"
$env:GAME_EXP_MCP_PATH="/mcp"
# Write tools over HTTP are read-only by default unless this endpoint is truly single-principal:
$env:GAME_EXP_MCP_TRUSTED_SINGLE_PRINCIPAL="1"
python tools/game-exp/mcp_server.py
```

The local MCP endpoint is then `http://127.0.0.1:8765/mcp`. ChatGPT Web cannot connect to localhost directly. Do not enable `GAME_EXP_MCP_TRUSTED_SINGLE_PRINCIPAL=1` on an endpoint shared by multiple independent users: the current HTTP server does not bind a different GitHub principal per request. Connect this local endpoint through OpenAI Secure MCP Tunnel, or deploy the same Streamable HTTP server behind a trusted HTTPS endpoint. Do not expose the unauthenticated localhost listener directly to the public internet.

The HTTP mode is stateless and uses JSON responses. The existing stdio mode remains the default, so current Codex configuration does not change.

For ChatGPT Web, the game-exp MCP remains only the lifecycle control plane. Source edits and PR merge actions should use an authorized GitHub/code capability in the host. After the MCP endpoint is registered in ChatGPT developer mode and its tools scan successfully, package or associate the `plugins/game-exp` Skill so the model preserves Review, PROMISING, SELECTED, reconciliation, and Archive gates.

Full write/modify MCP availability and developer-mode permissions depend on the current ChatGPT plan and workspace policy. If the workspace cannot enable write-capable custom MCP apps, the server can still be developed and tested, but the end-to-end web workflow cannot match Codex write behavior yet.
The Harness-native text Board is implemented and validated. Only the graphical MCP Apps Board remains deferred: on the tested Codex 0.155.1 host, MCP Apps rendering remains behind disabled under-development feature flags. Any future graphical Board must consume the same read-only projection without becoming the authority.


## Source initialization

After a valid `experiment.bind` request has produced authoritative Binding/Manifest/State records in the protected Ledger, initialize source refs with:

```powershell
python tools/game-exp/cli.py --repo owner/repo initialize EXP-21 --request-id req-init-21
```

The client supplies only the canonical experiment ID. The trusted workflow reconstructs all other inputs from `game-exp/ledger`, verifies the original bound request digest and manifest digest, then atomically creates:

- `refs/heads/exp/<issue>` at a new initialization commit whose only change is the deterministic source manifest;
- an annotated `refs/tags/exp-base/<issue>` that still points to the frozen parent commit and records the initialization commit + initialization-plan digest in its tag message.

Re-running initialization is safe. If the experiment branch has advanced normally, the initializer verifies that the current branch is descended from the recorded initialization commit. Partial or mismatched refs fail closed and are never force-overwritten.


## Trusted Integration

Integration is deliberately two-phase. A selected experiment is not considered integrated merely because a Rehearsal exists.

Create or reuse the exact Integration PR:

```powershell
python tools/game-exp/cli.py --repo siskosun/toy2game --json integrate EXP-21 --request-id req-integrate-21
```

The proposal workflow requires the current lifecycle to be `SELECTED`, requires the current Rehearsal to target the current `main`, and creates a deterministic branch:

`game-exp/integration/<issue>/<rehearsal-id>`

The proposal commit has exactly one parent (the rehearsed main) and its tree is exactly the trusted Rehearsal `integration_tree_sha`. The workflow opens a normal PR against `main`; it does not mark the experiment integrated and it does not bypass the protected-main PR rule.

After that PR is actually merged, finalize it:

```powershell
python tools/game-exp/cli.py --repo siskosun/toy2game --json integrate-finalize EXP-21 --pr-number 77 --request-id req-integrate-finalize-21
```

The trusted finalize workflow independently verifies the merged PR, head tree, merge tree, merge ancestry in current main, workflow identity, current Candidate and current Rehearsal. Only then does the protected Ledger receive `integration.register` and lifecycle change from `SELECTED` to `INTEGRATED`.

If `main` advances before the Integration PR is prepared, run a new Rehearsal with a new logical operation id, for example `rehearse EXP-21 --request-id req-rehearse-21-refresh-1`. SELECTED experiments are allowed to refresh their Rehearsal without changing lifecycle.


## Cross-interface operation recovery

game-exp public contract v1.0 treats MCP, CLI and GitHub Bridge as replaceable transports around the same trusted semantics.

Before a mutation, prefer MCP, then CLI, then Bridge. Once a mutation is accepted or uncertain, do not create a replacement operation merely because another interface is available.

Every mutation uses one stable request/operation id. Async actions first commit a protected `execution.claim` that binds the action, exact arguments, experiment-state digest and Trusted Writer-verified GitHub actor. Worker workflows reject invocations that do not match that claim and remotely deduplicate duplicate workflow runs by request id.

A lost MCP reply can be recovered from CLI without repeating the mutation:

```powershell
python tools/game-exp/cli.py --repo owner/repo --json get-operation req-123
python tools/game-exp/cli.py --repo owner/repo --json resume-operation req-123
```

`resume-operation` only resumes an already committed execution claim. If the experiment state changed after the claim, it returns a conflict instead of dispatching against the new state.

Authorization failure is not a transport failure and must not trigger an MCP -> CLI -> Bridge bypass attempt.

## Complete project initialization

v0.14 adds a hard `PROJECT_READY` gate for new repositories. Copying game-exp files is no longer considered setup completion.

After bootstrap files are committed to `main`:

```powershell
python tools/game-exp/cli.py --repo owner/repo --json project-preflight
python tools/game-exp/cli.py --repo owner/repo --json project-init
```

`project-init` completes Ledger initialization, Trusted Writer repository credentials, Immutable Releases, hardened Actions defaults, the four verified rulesets, Trusted Writer self-test, and a final repo-level Doctor. It succeeds only when Doctor is PASS.

Private repositories whose GitHub plan does not support repository rulesets fail at preflight with `RULESETS_PLAN_UNSUPPORTED`; game-exp does not silently weaken the trust model or make a repository public.

## Harness conformance simulator

v0.13 adds a synthetic behavior-screening mode for testing Agents/Harnesses without touching GitHub or the protected Ledger.

The standing suite checks six critical behaviors:

- recover the same operation id after a lost/UNKNOWN response;
- do not bypass authorization failure by switching interfaces;
- do not reuse an old PASS Review for a new Candidate;
- refresh stale Rehearsal before Integration and confirm the same operation id;
- treat archived dependencies as review-required rather than automatically invalid;
- never let automated checks replace human Review/selection gates.

Inspect the fixed suite:

```powershell
python tools/game-exp/cli.py --json conformance-suite
```

Start one scenario:

```powershell
python tools/game-exp/cli.py --json conformance-start lost-response-recovery `
  --session-file .game-exp/conformance/lost.json
```

Then use the normal CLI surface against the synthetic session:

```powershell
python tools/game-exp/cli.py --conformance-session .game-exp/conformance/lost.json `
  --json get-operation req-archive-42
```

Evaluate it:

```powershell
python tools/game-exp/cli.py --json conformance-result `
  --session-file .game-exp/conformance/lost.json
```

The same session file can be used by MCP by setting:

```text
GAME_EXP_CONFORMANCE_SESSION=<session.json>
```

The normal `game_exp_*` tools then return synthetic results and record the surface used. This enables a lost MCP response to be recovered through CLI in one trace.

Aggregate all six evaluated sessions with `conformance-report`. Only a complete critical-suite PASS produces `eligible_for_real_repo_test=true`.

Keep the last released report as the incumbent baseline and compare it to a candidate report with `conformance-compare`. Reports with different `suite_digest` values are intentionally non-comparable; rerun both implementations after changing the evaluator suite.

Simulator PASS is only a pre-screen for real-repository validation. It is never Candidate, Review, Rehearsal, Integration, Archive, or release evidence.

## Resumable notifications

Use `notifications --after <checkpoint>` for incremental polling. If a response has `next_cursor`, process every page first. Persist `checkpoint_cursor` only after the final page succeeds. Event delivery is external and deduplicates by `event_id`.

## Portable prototype handoff

Handoff schema v2 binds implementation work to a Ledger snapshot and source identity. Returned Godot evidence must identify build identity, source SHA, check environment, evidence scope, artifact location/digest and whether that location is portable across Harnesses.
