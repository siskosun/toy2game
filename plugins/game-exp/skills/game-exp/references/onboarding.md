# game-exp first-use onboarding

## Goal

Help a new user reach a valid first experiment without requiring them to understand Ledger internals, request ids, lifecycle enums, or protected refs.

Onboarding is guidance only. It must not bypass repository trust checks, human gates, or trusted workflow tools.

## When to trigger

Show first-use onboarding when any of these is true:

- the protected Ledger contains zero experiments;
- the user explicitly asks how to start or says they are using game-exp for the first time;
- the user opens the Board and no valid experiment exists yet.

Do not repeatedly force onboarding after the repository already has experiments. Provide a visible `新手引导` entry instead.

## Six-step flow

1. `连接检查`
   - run `game_exp_access_check` first and classify access as `NO_ACCESS`, `READ_ONLY`, `WRITE`, or `ADMIN`;
   - `NO_ACCESS`: explain that the repository cannot be read and stop;
   - `READ_ONLY`: allow Board viewing but disable creating or advancing experiments; ask for repository write permission or another repository;
   - `WRITE`: allow normal game-exp use and label admin-only checks as partial when unavailable;
   - `ADMIN`: allow normal use with full repository-level inspection coverage;
   - then run repo-level `game_exp_doctor` without `experiment_id`;
   - explain any partial coverage;
   - do not continue past a hard trust failure.

2. `描述第一个实验`
   - ask for or infer: target prototype/subject, intended change, and desired player outcome;
   - do not ask the user to write a Manifest.

3. `生成实验定义`
   - draft hypothesis, success criteria, kill criteria, scope, subject, runtime, and review protocol;
   - ask only when a missing user-owned choice would materially change the experiment;
   - otherwise present the drafted definition in plain Chinese.

4. `建立实验`
   - resolve/create the real GitHub Issue through the host's authorized GitHub capability;
   - Bind through `game_exp_experiment_bind`;
   - reconcile ACCEPTED/UNKNOWN until authoritative;
   - Initialize through `game_exp_initialize`.

5. `开发与试玩`
   - source work happens on the canonical `exp/<issue>` branch;
   - when implementation is ready, move to REVIEW and build Candidate;
   - explain that automated checks mean “ready to review,” not “experiment passed.”

6. `人工决定`
   - user supplies PASS/FAIL Review;
   - PASS does not auto-promote;
   - if the user explicitly promotes, continue with Rehearsal -> Selection -> Integration;
   - when finished, archive with explicit branch-retention choice.

## Inline UI

When Chat inline UI is supported, empty repositories should render an onboarding card instead of only `暂无实验`.

Show:

- progress: `1/6` through `6/6`;
- current step title;
- one-sentence explanation;
- one primary next action;
- a compact `为什么需要这一步` expandable explanation;
- `跳过新手引导` only as a presentation choice, never as a way to skip required trust or lifecycle gates.

For repositories that already contain experiments, expose `新手引导` as an optional help entry and do not interrupt the normal Board.

## Natural-language entry points

Accept simple user intents such as:

- `第一次用 game-exp`
- `帮我创建第一个实验`
- `我要给 flip-match 做一个实验：失配后提示下一位玩家`
- `这个实验开发完成，进入评审`
- `我试玩过了，PASS`

Translate these intents to the existing trusted workflow. Do not require users to name MCP tools or lifecycle enums.

## Completion

Onboarding is complete only when the first experiment is authoritatively bound and initialized. Later lifecycle stages remain part of normal game-exp operation.

Do not label an ACCEPTED dispatch as onboarding completion.

## Access-state copy

Use these Chinese messages consistently:

- `NO_ACCESS`: `无法访问该 GitHub 仓库。请检查登录、仓库授权，或切换到你有权限的仓库。`
- `READ_ONLY`: `当前只有读取权限：可以查看 game-exp 面板，但不能创建或推进实验。请获得仓库写入权限后继续。`
- `WRITE`: `当前具有读写权限，可以使用 game-exp；部分管理员级检查可能不可见。`
- `ADMIN`: `当前具有完整仓库管理权限。`
