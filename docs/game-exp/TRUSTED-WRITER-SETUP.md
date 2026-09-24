# game-exp Trusted Writer

Repository: `siskosun/toy2game`

## Current status

**ACTIVE_AND_VERIFIED**

The trusted write path is configured and has passed the V1.3 Phase 1 gate.

- one write-capable Deploy Key is registered as `game-exp trusted writer`;
- repository secret `GAME_EXP_WRITER_KEY` is configured;
- the Ledger Ruleset permits Deploy Key bypass and rejects ordinary users / `GITHUB_TOKEN`;
- `main` is PR-only and rejects delete / force-push;
- the Trusted Writer source is checked out at `${{ github.workflow_sha }}`;
- the Trusted Writer has passed normal commit, idempotent replay, request-ID conflict, stale-head conflict, lost-response recovery, and negative ordinary-token tests.

Primary evidence:

- Phase 1 selftest: https://github.com/siskosun/toy2game/actions/runs/35951146384
- post-refactor selftest: https://github.com/siskosun/toy2game/actions/runs/35952248853
- Phase 1 report: `docs/game-exp/PHASE1-RESULTS.md`

Do not paste, commit, log, or attach the private Deploy Key.

## Normal verification

Run:

```powershell
python tools/game-exp/cli.py --repo siskosun/toy2game --json doctor
```

An administrator-capable local GitHub credential should report PASS for:

- Ledger ref;
- required Rulesets;
- exactly one write Deploy Key named `game-exp trusted writer`;
- `GAME_EXP_WRITER_KEY` repository secret;
- Immutable Releases.

A restricted GitHub Actions token may report admin-only settings as `UNKNOWN`. That is not evidence that the controls are missing.

## Rotation

If the Deploy Key must be rotated:

1. Generate a new Ed25519 key pair locally.
2. Add the new public key to the repository as a write Deploy Key.
3. Replace repository secret `GAME_EXP_WRITER_KEY` with the new private key.
4. Run the Trusted Writer selftest.
5. Verify the new key can write the protected Ledger while ordinary `GITHUB_TOKEN` still fails with Ruleset rejection.
6. Delete the old Deploy Key.
7. Delete any unnecessary local copy of the old private key.

Never disable the Ledger Ruleset merely to simplify key rotation.

## Fail-closed behavior

If the key or secret is unavailable, Trusted Writer mutations must fail. The client must report a proven rejection or `UNKNOWN` when the remote outcome cannot be established; it must never fall back to direct Ledger writes.
