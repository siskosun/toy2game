# game-exp complete project setup

## Completion contract

A newly created repository is not a game-exp project merely because the plugin,
CLI, MCP server, and workflows were copied into it.

Call the repository `PROJECT_READY` only when all of the following are true:

- `game-exp/ledger` exists;
- exactly one write-capable Deploy Key named `game-exp trusted writer` exists;
- repository secret `GAME_EXP_WRITER_KEY` exists;
- GitHub Immutable Releases are enabled;
- all four verified rulesets are active:
  - `game-exp ledger`;
  - `game-exp experiment branches`;
  - `game-exp immutable refs`;
  - `game-exp protected main`;
- repository Actions default `GITHUB_TOKEN` permission is read-only and Actions
  cannot approve pull requests;
- the Trusted Writer self-test succeeds;
- repo-level `game_exp_doctor` returns `PASS`.

Anything less is incomplete.

## New-project sequence

When the user asks game-exp to create or prepare a new repository:

1. Create/initialize the repository and source project.
   - For a locked Node/npm project, bootstrap can infer project policy from
     `package.json` plus `package-lock.json` / `npm-shrinkwrap.json`.
   - For any other project type, create an explicit valid
     `.game-exp/project-policy.json` first. Do not let bootstrap guess Node/npm.
2. Install game-exp runtime/Skill/workflows with `bootstrap.py`.
3. Commit and push those files to `main`.
4. Run `game_exp_project_preflight` or CLI `project-preflight`.
5. If preflight is not PASS, stop before creating trust credentials or protected
   refs. Do not claim the project is ready.
6. Run `game_exp_project_init` through local stdio MCP, or CLI
   `project-init`, with the repository administrator's GitHub principal.
7. The initializer creates/repairs the required trust controls idempotently,
   runs the Trusted Writer self-test, then runs Doctor.
8. Declare `PROJECT_READY` only when the initializer returns
   `status=PASS` and `complete=true`.
9. Only then start first-experiment onboarding.

## Project policy gate

Repository trust setup and project build policy are separate, but both must be
valid before first Candidate/Rehearsal execution.

Project policy schema v2 is recommended:

- `node-npm` requires an exact `toolchain.node_version` and receives trusted
  `actions/setup-node` setup;
- other adapters use argv-only install/test/build commands with no implicit
  runtime setup;
- an unknown clean repository type must fail bootstrap with an explicit policy
  requirement instead of receiving a guessed Node/npm policy.

A repository may keep a valid schema-v1 Node/npm policy for compatibility.
Newly generated policies use schema v2.

## GitHub plan gate

Rulesets and protected branches are available for public repositories on GitHub
Free, but private repositories require GitHub Pro/Team/Enterprise.

If GitHub responds that the repository must be public or the account upgraded:

- return `BLOCKED_PLAN / RULESETS_PLAN_UNSUPPORTED`;
- do not continue creating Ledger credentials or claim a degraded trust mode;
- surface the two material choices: make the repository public, or use a GitHub
  plan that supports private-repository rulesets;
- never change repository visibility without explicit user approval.

There is no "weak private Free" compatibility mode. game-exp's protected-Ledger
authority depends on enforceable repository protection.

## Admin identity boundary

Complete project setup is repository-administration work.

- `game_exp_project_preflight` is read-only and may be used to explain blockers.
- `game_exp_project_init` is allowed only through local stdio MCP.
- CLI `project-init` is also allowed locally.
- Shared/streamable HTTP MCP must reject project initialization even if it is a
  trusted single-principal endpoint.
- The initializer uses the local authenticated GitHub administrator principal.
- Generated private Deploy Key material is held only in a temporary local
  directory, sent directly into the repository Actions secret, and then deleted.
- Never log, return, attach, commit, or persist the private Deploy Key.

## Provisioning order

The order matters:

1. preflight admin access, ruleset availability, and committed game-exp workflow;
2. initialize the orphan protected Ledger ref;
3. establish the one Trusted Writer write Deploy Key plus repository secret;
4. enable Immutable Releases;
5. harden repository Actions defaults;
6. create the four verified rulesets using the exact production templates;
7. run the Trusted Writer self-test;
8. run Doctor.

Ledger creation happens before its creation rule becomes active. Rulesets are
installed only after the Trusted Writer credential exists so their DeployKey
bypass has a real trusted writer.

## Idempotency and repair

Rerunning `project-init` against a healthy project should preserve existing
controls and return PASS.

Fail closed when:

- another write-capable Deploy Key exists;
- multiple Trusted Writer write Deploy Keys exist;
- an existing game-exp ruleset has the right name but different semantics;
- repository administration permission is absent;
- plan support is insufficient;
- the Trusted Writer self-test fails;
- final Doctor is not PASS.

If the exact Trusted Writer key exists but its secret is missing, rotate that
single key during repair instead of pretending the secret is recoverable.

## Why self-test is required

Presence checks alone are insufficient. The self-test proves:

- Trusted Writer can advance the Ledger;
- same request + same payload replays idempotently;
- same request + different payload is rejected;
- stale Ledger head is rejected;
- lost-response recovery does not create a duplicate mutation;
- ordinary workflow authority cannot bypass Ledger protection.

A skipped self-test means the project remains `INCOMPLETE`.

## First experiment boundary

Repository setup and experiment onboarding are different phases.

`PROJECT_READY` means only that the trusted control plane is operational.
It does not create an experiment, approve a human gate, or imply any Candidate
quality.

After `PROJECT_READY`, continue with `references/onboarding.md`.
