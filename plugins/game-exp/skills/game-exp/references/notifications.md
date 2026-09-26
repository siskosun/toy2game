# game-exp collaboration notifications

## Purpose

Notify collaborators when meaningful experiment events happen without turning game-exp into a messaging platform.

Notification facts are derived from one pinned protected Ledger snapshot. Delivery is delegated to the host or an external adapter such as ChatGPT tasks, Feishu, Slack, email, or another team channel.

## Event source

Use `game_exp_notifications`.

Important events include:

- new experiment;
- enters Review;
- Candidate ready;
- human Review recorded;
- PROMISING;
- SELECTED;
- REJECTED;
- integrated;
- archived.

Each notification has a stable `event_id`. Delivery adapters MUST deduplicate by `event_id`.

## Recipients

For one subject/prototype, collaborators are the trusted initiators and GitHub-linked code contributors observed across its experiments.

For an event, exclude the event actor when that actor is known. The remaining collaborators are `targets`.

This means that when Alice creates a new experiment under a prototype where Bob has already initiated or contributed to another experiment, Bob is a target for the new-experiment notification.

Contributor identity is collaboration metadata only. It never grants lifecycle authority.

## Delivery boundary

game-exp does not send chat messages, emails, Slack messages, or Feishu messages itself.

It exposes replayable events and target identities. A delivery adapter:

1. polls or receives the event feed;
2. filters to its destination/user;
3. deduplicates by `event_id`;
4. sends through the external system;
5. persists its own delivery cursor/state.

A delivery failure must not alter experiment lifecycle state.

## Suggested Chinese message

```text
{subject_name} 有新的实验 {experiment_id}
发起人：{actor_or_initiator}
目标：{title}
当前事件：{event_label_zh}
```

Keep messages compact. Link to the experiment panel when the host supports it.
