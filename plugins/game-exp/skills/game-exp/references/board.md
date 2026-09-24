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

## 总览

Answer three questions first: what is broken, what needs a human decision, and what is currently active.

Order:

1. `需要处理`: use `views.overview.attention_ids`; order by Board attention priority.
2. `当前进行`: use `views.overview.active_ids`, excluding experiments already shown under `需要处理`.
3. One-line archived summary using `views.overview.archived_count`.

Do not dump Candidate/Rehearsal/Integration IDs in the default overview unless they explain the next action.

## 待处理

Use `views.attention.experiment_ids` only.

Include:

- health FAIL or UNKNOWN;
- archive recovery;
- human Review;
- promotion decision;
- general human lifecycle decision;
- human selection;
- archive-mode choice.

For each row show: experiment, prototype/subject, title, current stage, problem or gate, and next action.

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
