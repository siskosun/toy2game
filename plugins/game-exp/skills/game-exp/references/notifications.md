# game-exp collaboration notifications

## Purpose

Notify collaborators about meaningful experiment changes without turning game-exp into a messaging platform.

Events are deterministic projections of committed protected-Ledger snapshots. Delivery is delegated to ChatGPT tasks, Feishu, Slack, email, webhook, or another adapter.

## Event contract

Use `game_exp_notifications` or CLI `notifications`.

Every row includes:

- stable `event_id` for delivery deduplication;
- namespaced `event_type`;
- `event_version`;
- experiment and subject identity;
- actor when authoritative activity contains one;
- intended collaboration targets;
- `source_snapshot` identifying the Ledger snapshot used to rebuild the event.

Meaningful events include new experiments, Review entry, Candidate readiness, human Review, PROMISING, SELECTED, REJECTED, Integration, Archive, and `dependency.review_required`.

Contributor identity remains collaboration metadata only and never grants lifecycle authority.

## Access

When `viewer_login` is supplied, game-exp checks that viewer currently has repository collaboration access before returning the personalized feed. Independently, every generated target is filtered against current repository collaborator permission, including aggregate feeds without a viewer. This is a read-time access check, not durable future authority.

External adapters must also enforce the destination system's own authorization. A stale cached permission must not be treated as permission to deliver forever.

## Cursor protocol

Historical/incremental delivery uses two cursor forms.

- `cursor`: page continuation within one pinned snapshot.
- `checkpoint_cursor`: after all pages have been processed successfully, persist this as the external adapter's checkpoint.
- `after=<checkpoint_cursor>`: next polling cycle returns events that were absent from the checkpoint snapshot and exist in the newer committed snapshot.

Adapter algorithm:

1. request feed with prior `after` checkpoint;
2. process one page;
3. while `next_cursor` exists, request the next page using `cursor`;
4. deduplicate sends by `event_id`;
5. only after all pages succeed, persist `checkpoint_cursor`;
6. on a later poll, pass that value as `after`.

Never advance the checkpoint merely because one page was fetched. This avoids silent event loss after partial delivery failure.

If the referenced Ledger snapshot can no longer be reconstructed, game-exp returns `CURSOR_EXPIRED`. The adapter must explicitly resynchronize rather than silently treating the feed as empty.

## Rebuild and retention

Events are rebuilt from immutable committed Ledger history rather than stored as a second lifecycle state machine. Delivery state, retries, read receipts and subscription preferences remain external.

The retention model is therefore bounded by availability of the protected Ledger history used by the cursor. No message queue is required by game-exp itself.

## Suggested Chinese notification

```text
{subject_name} 有新的实验动态
实验：{experiment_id}
发起人/操作者：{actor}
事件：{event_label_zh}
目标：{title}
```

Keep messages compact and link to the experiment panel when supported.
