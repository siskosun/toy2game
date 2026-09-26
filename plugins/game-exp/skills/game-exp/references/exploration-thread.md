# game-exp exploration thread contract

## Purpose

An exploration thread groups experiments that investigate the same product/design question over time.

It is NOT a command to generate multiple variants in parallel.

## Default behavior

Prototype work may change the underlying creative idea, core loop, or product direction. Therefore:

- default to one active experiment for one exploration thread;
- do not automatically fan out 3-5 variants;
- a later experiment may replace, supersede, or reinterpret an earlier one;
- preserve the historical chain so later collaborators can see what was tried and what was learned.

## Current representation

Until a dedicated authoritative exploration object is introduced, use:

- stable `manifest.subject` for the prototype identity;
- `manifest.relationships` for explicit `depends_on`, `blocks`, or `supersedes` links;
- titles/hypotheses to describe the question being explored.

The Board may present a human-facing “探索主题” grouping when the relationship/history is clear, but must not invent an authoritative exploration id.

A future protocol version may add a stable exploration id only if repeated use demonstrates that subject + relationships are insufficient.
