# game-exp -> Godot Prototype Studio handoff

## Boundary

game-exp owns experiment identity, scope, lifecycle, evidence references, human gates, and selection.

Godot Prototype Studio owns prototype implementation, runtime verification, export, and requested playtest delivery.

game-exp must not rebuild Godot editing/export/publishing capabilities.

## Handoff

Use `game_exp_prototype_handoff(experiment_id)` to create a pinned implementation brief containing:

- canonical experiment branch and parent SHA;
- subject/prototype root;
- title and hypothesis;
- success criteria;
- kill criteria;
- allowed and avoided scope;
- runtime requirements;
- review protocol.

Pass this package to Godot Prototype Studio when implementation or playable delivery is needed.

## Return evidence

Godot Prototype Studio should return:

- exact source SHA;
- checks actually run;
- playable status;
- delivery evidence only when delivery was requested.

These are implementation/runtime facts. They do not automatically authorize Review PASS, PROMISING, SELECTED, merge, or Archive.

After implementation evidence is available, game-exp resumes the normal Candidate/Review lifecycle.
