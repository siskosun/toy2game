# game-exp Board information architecture

## Default view

When the user opens the panel without naming a view, render `总览`.

Show five logical views:

- `总览`
- `待处理`
- `原型`
- `分支图`
- `归档`

Use one pinned protected Ledger snapshot for every view in the same response.

## Shared header

Show:

- `仓库`: full `owner/repo`.
- `Ledger 快照`: pinned commit SHA.
- `实验统计`: total experiments, active count, archived count, attention count, abnormal-health count.

## 聚焦筛选

The Board may include a read-only `focus` projection. It never changes the protected Ledger snapshot or any lifecycle state.

Supported filters:

- `query`: case-insensitive substring search across experiment id, Issue number, title, subject/prototype name, and hypothesis.
- `subject_id`: exact stable subject id.
- `lifecycle`: lifecycle code, normalized to uppercase.
- `attention_only`: only experiments that currently require attention.

When any filter is active, render `focus.summary_zh` and use `focus.experiment_ids` to narrow the displayed rows. The focus projection also carries `attention_count`, `counts_by_lifecycle`, and `counts_by_health` for the narrowed result. Keep global counts and the underlying five views based on the complete pinned snapshot so filtering cannot hide repository health or change authority.

If the focus result is empty, show `没有符合当前筛选条件的实验`; do not report `暂无实验` unless the repository itself has zero experiments.

## 总览

Answer three questions first: what is broken, what needs a human decision, and what is currently active.

Order:

1. `需要处理`: use `views.overview.attention_ids`; order by Board attention priority.
2. `当前进行`: use `views.overview.active_ids`, excluding experiments already shown under `需要处理`.
3. One-line archived summary using `views.overview.archived_count`.

Do not dump Candidate/Rehearsal/Integration IDs in the default overview unless they explain the next action.

## 待处理

Use `views.attention.sections` as the primary structure instead of one flat list.

Render sections in this order when non-empty:

1. `异常`
2. `需要恢复`
3. `需要你评审`
4. `需要你决策`
5. `需要选择归档方式`

Each experiment entry should show:

- 实验编号
- 原型/主体
- 实验标题
- 当前阶段
- 为什么需要处理
- 下一步动作

Use the Chinese values already emitted in `display` and `attention`. Do not expose raw enums such as `HUMAN_REVIEW` as the primary UI text.

## 原型

Use `views.prototypes.groups`.

Render one compact group per subject:

- subject name and root path;
- total / active / archived / attention counts;
- lifecycle distribution;
- latest experiment;
- child experiment ids only when useful.

Hierarchy is `Repository -> Subject/Prototype -> Experiment`.

Authoritative `manifest.subject` wins. `scope.allowed` inference is legacy fallback only.
If a legacy manifest has only broad `games/**` scope or no resolvable prototype, display `仓库级/未指定原型` rather than inventing a prototype identity.

For each prototype group, show `recent_activity` as `最近活动` and `relationship_count` when non-zero. Activity labels must remain Chinese.

## 分支图

Use `views.branches.lanes`.

Render a lightweight text topology rather than a wide table. Example:

```text
main
├─ exp/51  原型A  REVIEW  Candidate C-51-...
├─ exp/52  原型B  PROMISING  Rehearsal R-52-...
└─ exp/53  原型C  ARCHIVED  final tag exp-final/53
```

Show parent SHA only when diagnosing freshness or ancestry. Show final tag for archived experiments. Do not imply a branch still exists after `ATOMIC_DELETE` merely because the canonical branch ref is recorded in binding metadata.

## 实验详情

When the user opens one experiment, use `game_exp_experiment_panel` when available and organize the returned detail panel in this order:

1. `实验概况`: 标题、原型、阶段、健康、下一步。
2. `假设与判定`: use `judgement.hypothesis`, `judgement.success_criteria`, and `judgement.kill_criteria`.
3. `活动时间线`: use the experiment `activity` array. Render `label_zh` and `detail_zh`; show `occurred_at` only when the Ledger-derived record contains a trustworthy timestamp.
4. `关系`: show outgoing and incoming experiment relations with Chinese relation labels.
5. `代码与证据`: branch / Candidate / Rehearsal / PR / Archive records on demand.

Relationship labels:

- `depends_on` -> `依赖`
- incoming `depends_on` -> `被依赖`
- `blocks` -> `阻塞`
- incoming `blocks` -> `被阻塞`
- `supersedes` -> `替代`
- incoming `supersedes` -> `被替代`

Do not fabricate timestamps for timeline events. Events without an authoritative time may still appear in semantic lifecycle order.

## 中文展示约束

All system-generated panel entries must use Chinese as the primary text: view names, section names, lifecycle labels, health labels, next actions, relationship labels, and activity labels.

Raw machine enums may appear only in debugging context or parentheses when they materially help diagnosis. User-authored or authoritative stored titles are not silently translated or rewritten.

## 归档

Use `views.archive.experiment_ids`.

Show experiment, subject/prototype, title, Archive id, Integration id, and final-tag ref when available. Keep this view separate from active work by default.

## Chinese labels

Lifecycle:

- `ACTIVE` -> `进行中`
- `REVIEW` -> `评审中`
- `PROMISING` -> `待选择`
- `SELECTED` -> `已选定`
- `INTEGRATED` -> `已集成`
- `REJECTED` -> `已拒绝`
- `ARCHIVED` -> `已归档`

Health:

- `PASS` -> `正常`
- `FAIL` -> `异常`
- `UNKNOWN` -> `未知`

Next gate:

- `IMPLEMENT_OR_REVIEW` -> `继续实现 / 进入评审`
- `CANDIDATE_BUILD` -> `构建候选版本`
- `HUMAN_REVIEW` -> `人工评审`
- `HUMAN_PROMOTION` -> `决定是否晋级`
- `HUMAN_DECISION` -> `人工决策`
- `TRUSTED_REHEARSAL` -> `可信彩排`
- `HUMAN_SELECTION_OR_REFRESH_REHEARSAL` -> `人工选择 / 必要时刷新彩排`
- `TRUSTED_INTEGRATION_OR_REFRESH_REHEARSAL` -> `集成 / 必要时刷新彩排`
- `ARCHIVE_OR_RETAIN` -> `选择归档方式`
- `ARCHIVE_RECOVERY` -> `恢复归档`
- `ARCHIVE` -> `归档`
- `TERMINAL_NEW_EXPERIMENT_FOR_NEW_WORK` -> `已结束；新工作需新建实验`
- `VERIFY_EXPERIMENT_HEALTH` -> `核验实验健康`
- `DO_NOT_USE_RECREATE_EXPERIMENT` -> `禁止继续；重建实验`

Keep raw non-PASS health codes visible for diagnosis.

## Empty repository

If the protected Ledger contains zero experiments, show the repository header and `暂无实验`, with next action `新建实验`. Do not fabricate a placeholder experiment row.
