# game-exp Phase 1 target-repository results

Repository: `siskosun/toy2game`  
Upstream: `asmoyou/toy2game`  
Target baseline SHA: `39078515a13331a748e63bc75424ed83c773a72a`

## Decision

**REQUIRED_CHANGES**

The protocol reference implementation passes locally, and the target repository now validates the critical GitHub primitives for CAS, archive safety, cross-platform canonicalization, artifact attestation, and immutable retention. Phase 1 is not frozen yet because the Trusted Ledger Writer cannot be executed until a dedicated long-lived credential is manually installed, and the trusted workflow source still needs default-branch governance.

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
| Trusted Writer credential installed | BLOCKED | No write Deploy Key / `GAME_EXP_WRITER_KEY` secret installed by this automation environment |
| Trusted Writer can bypass Ledger Ruleset | UNKNOWN | Must be tested after the manual credential step |
| Trusted workflow source protection | REQUIRED | Main/default branch governance is not yet frozen |

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

## Remaining Phase 1 gate

Before changing this decision to `PROTOCOL_FREEZE_APPROVED`:

1. Manually register the generated public key as a write Deploy Key.
2. Store its private key as repository secret `GAME_EXP_WRITER_KEY`.
3. Run the trusted writer workflow and prove:
   - ordinary user/GITHUB_TOKEN write remains rejected;
   - Deploy-Key workflow write succeeds;
   - same request/same payload is idempotent;
   - same request/different payload is rejected;
   - stale `expected_head` is rejected;
   - lost-response retry reconciles to the committed remote record.
4. Protect the default branch/workflow source so normal project code cannot silently replace the trusted writer or attestation workflow.
5. Re-run the target checks after those controls are active.

Any failure in items 3-4 remains a high-severity correctness failure.
