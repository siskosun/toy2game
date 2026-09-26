# game-exp Chat inline UI contract

## Purpose

When the host supports a self-contained inline interactive app surface, render the game-exp Board as an interactive Chinese panel directly in the chat instead of expanding every Board field into prose.

This UI is a read-only projection. The protected Ledger and trusted workflow tools remain authoritative.

## Data sources

Use only one pinned protected Ledger snapshot per rendered panel.

Preferred read APIs:

1. Repository level: `game_exp_board`
2. Subject/prototype level: `game_exp_subject_panel`
3. Experiment level: `game_exp_experiment_panel`

Never combine data from separately moving snapshots inside one rendered panel.

## Navigation hierarchy

The inline panel must support:

`仓库总览 -> 原型/主体 -> 单实验`

The user must be able to return to the previous level without a new remote mutation.

## Repository view

Provide five tabs:

- 总览
- 待处理
- 原型
- 分支图
- 归档

At minimum support local filtering by:

- keyword
- subject/prototype
- lifecycle
- attention only

Filtering changes presentation only. It must not change the Ledger snapshot, authority, counts in the complete repository projection, or lifecycle state.

## Subject view

Show:

- stable subject/prototype name and root path
- experiment count
- active / archived / attention counts
- lifecycle and health distribution
- recent activity
- child experiments
- relationship edges touching the subject

Selecting a child experiment opens the single-experiment view.

## Experiment view

Show in this order:

1. 实验概况
2. 假设与判定
3. 活动时间线
4. 关系
5. 代码与证据

The primary labels must be Chinese. Raw enums and ids may appear as secondary diagnostic detail.

## Interaction boundary

The inline UI may perform only local presentation actions by itself:

- tab switching
- local filter/sort
- expand/collapse
- repository -> subject -> experiment navigation
- returning to parent view

It must not directly mutate lifecycle state or protected refs.

For actions such as Review, PROMISING, SELECTED, Integration, or Archive, the UI may show the currently valid next action, but execution must be handed back to the trusted MCP/workflow path and must preserve existing human gates.

## Empty and failure states

For a zero-experiment repository, prefer the first-use onboarding card from `onboarding.md` over a bare empty state. The card may show `暂无实验` as context, but its primary action is `创建第一个实验`.

- zero repository experiments: `暂无实验`
- zero filtered experiments: `没有符合当前筛选条件的实验`
- health FAIL: show as blocked; do not present normal lifecycle actions as available
- UNKNOWN: show unresolved and preserve the same authoritative request/snapshot identity

## Text fallback

If the host cannot render an interactive inline app, fall back to the text Board rules in `board.md` without changing data semantics.

The interactive UI is a presentation layer, not a new protocol or authority layer.
