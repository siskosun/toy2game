# game-exp Phase 1 target-repository results

Repository: `siskosun/toy2game`  
Upstream: `asmoyou/toy2game`  
Target baseline SHA: `39078515a13331a748e63bc75424ed83c773a72a`

## Decision

**PROTOCOL_FREEZE_APPROVED**

The protocol reference implementation passes locally, and the target repository validates the critical GitHub primitives for CAS, archive safety, cross-platform canonicalization, artifact attestation, immutable retention, default-branch workflow governance, and Trusted Ledger mutation. The dedicated Deploy Key and repository secret are installed, and the Trusted Writer has passed normal commit, idempotent replay, request-id conflict, stale-head conflict, lost-response recovery, and negative ordinary-token tests.

## Reference protocol tests

Re-run on the preserved Phase 1 reference implementation:

- F01-F50 scenario suite: **50/50 PASS**
- Full unit/integration suite: **83/83 PASS**
- Scenario runtime: 4.259 s
- Full suite runtime: 6.701 s

These tests validate the protocol/state-machine implementation. The target-repository tests below validate GitHub-specific assumptions against the actual fork.

## Target repository evidence

| Gate | Result | Evidence |
|---|---|---|
| Fork exists and is writable | PASS | `siskosun/toy2game` |
| GitHub Issues usable as projection | PASS | Issues were enabled; duplicate-operation probes are closed Issues #1 and #2 |
| Duplicate Issue discovery | PASS | Both Issues with the same operation marker are discoverable after indexing delay |
| Remote non-force CAS | PASS | Competing commit B was rejected as non-fast-forward after A won |
| Ledger ref protection | PASS | Ruleset `23915844`, active |
| Experiment branch protection | PASS | Ruleset `23915886`, active; forward push allowed, force-push/delete rejected |
| Base/final tag protection | PASS | Ruleset `23915887`, active; ordinary protected-tag creation rejected |
| Ordinary GitHub Actions token can write Ledger | PASS (must reject) | Run 35949856108 failed with GH013 / Cannot update this protected ref |
| Atomic archive stale-executor race | PASS | Run https://github.com/siskosun/toy2game/actions/runs/35949395197 |
| Windows/macOS/Linux canonicalization | PASS | Run https://github.com/siskosun/toy2game/actions/runs/35949452925 |
| Candidate build isolation | PASS | Build, observer and attest/retain are separate jobs in run 35949568658 |
| Artifact provenance attestation | PASS | https://github.com/siskosun/toy2game/attestations/49730498 |
| Level-3 immutable retention | PASS | https://github.com/siskosun/toy2game/releases/tag/game-exp-candidate-35949568658 |
| Immutable asset deletion attack | PASS (must reject) | GitHub returned 422: Cannot delete asset from an immutable release |
| Immutable releases setting | PASS | Enabled for repository |
| Trusted Ledger Writer workflow | IMPLEMENTED | `.github/workflows/game-exp-trusted-writer.yml` |
| Trusted Writer credential installed | PASS | Write Deploy Key `game-exp trusted writer` + `GAME_EXP_WRITER_KEY` repository secret |
| Trusted Writer can bypass Ledger Ruleset | PASS | Self-test run 35951146384; production runs 35951186950 and 35951266376 |
| Trusted workflow source protection | PASS | Ruleset `23916012`: PR required on `main`, delete/force-push blocked |

## Atomic archive evidence

GitHub-hosted runner reproduced the stale-executor race:

- executor expected branch A: `2fbc9e8a4041be92c5baa17848651d7ca29636a2`
- concurrent writer advanced branch to B: `448ae412137dc4e2150d2c2ca072bf14ad328474`
- stale atomic archive attempted create-final-tag + delete-branch with lease A
- branch deletion failed with `stale info`
- tag creation failed with `atomic push failed`
- remote branch remained exactly B
- final tag did not exist

This validates the V1.3 archive race requirement on the real GitHub remote.

## Cross-platform evidence

GitHub-hosted Ubuntu, Windows and macOS runners all passed the same protocol probe. The final compare job confirmed:

- identical canonical digest on all three operating systems;
- safe integer max accepted;
- 2^53 rejected;
- NFD path rejected;
- Windows reserved names rejected;
- backslash path rejected;
- case-fold collision rejected.

## Candidate / retention evidence

Run 35949568658 used separated jobs:

1. `build`: checked out source, ran `npm ci`, project tests and the real toy2game build, then uploaded `candidate.tgz`.
2. `observe`: no source checkout; downloaded the candidate and inspected the packaged artifact.
3. `attest-retain`: no project execution; re-computed SHA-256, created GitHub build provenance, verified it, published the artifact into an immutable release, and attempted an asset deletion attack.

Trusted digest:

`sha256:2c28be0b16f336624398e533fb2226bc20990cb1c4750fbbaa0f7dec6d42c79a`

The deletion attack was rejected by GitHub. This is sufficient evidence that GitHub Immutable Releases can serve as the Level-3 backend for this public pilot repository.

## Repository-specific baseline issue

On the local Windows machine, `npm run build` currently fails in `scripts/build.mjs` with `spawn npm ENOENT`. The same real build passes on the Ubuntu GitHub-hosted runner. This is a toy2game Windows baseline compatibility issue, not a game-exp protocol failure.

## Active repository configuration

- Issues: enabled
- Immutable releases: enabled
- Default Actions token permission: read
- Actions PR approval permission: disabled
- Ledger Ruleset: active, DeployKey bypass only
- Experiment branch Ruleset: active, DeployKey bypass only
- Protected base/final tag Ruleset: active, DeployKey bypass only
- Trusted Writer workflow: committed
- Trusted Writer setup guide: `docs/game-exp/TRUSTED-WRITER-SETUP.md`
- Protected main Ruleset: active (`23916012`), PR required, delete/force-push blocked

## Trusted Writer final evidence

- Self-test workflow: https://github.com/siskosun/toy2game/actions/runs/35951146384
  - normal commit: PASS
  - same request / same payload idempotent replay: PASS
  - same request / different payload: PASS (rejected as `REQUEST_ID_CONFLICT`)
  - stale expected head: PASS (rejected as `HEAD_CONFLICT`)
  - lost-response retry: PASS (recovered as replayed `COMMITTED`)
  - ordinary `GITHUB_TOKEN` write: PASS (rejected by GH013 / protected ref)
- Production Trusted Writer normal commit: https://github.com/siskosun/toy2game/actions/runs/35951186950
- Production idempotent replay: https://github.com/siskosun/toy2game/actions/runs/35951266376
- Production request-id conflict: https://github.com/siskosun/toy2game/actions/runs/35951296014 (expected workflow failure with `REQUEST_ID_CONFLICT`)
- Self-test evidence artifact: `game-exp-trusted-writer-selftest-35951146384`, SHA-256 `82a0eef88e005790ccfa64e2876e2c7cd001791246c0bc7ffcfb9ff1747c99eb`
- Durable Ledger records exist for both normal and lost-response probes; the stale-head probe produced no Ledger record.

## Phase 1 freeze decision

All V1.3 Phase 1 protocol gates required for this target repository have now passed. The repository may advance to the next phase under the frozen protocol.

**Decision: `PROTOCOL_FREEZE_APPROVED`**

