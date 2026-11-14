# The golden test levels

Small scenes of engine primitives that the production checks are measured on: each level is built
by a `scene.py` that the engine runs headless, and recorded by a job as the files a stage writes
(brief section 16, "Production files"). The engine version, its flags and the seeds are set when the
engine is pinned; the frame counts, the resolution (640 by 360 at 24 fps) and the primitives are here.

## Units

A unit is one directory that one job records. It holds `scene.py` and, once recorded,
`manifest.json`, `state.json` and `render.json`; `prop-drop/` also holds `rerun.json`, the state of
a second run of the same job. `two-shot/` holds only its two shots, one unit each.

| Unit | Frames | Stage | Checks it serves |
|---|---|---|---|
| `prop-drop` | 0 to 47 | `simulation` | `assets`, `simulation` |
| `two-shot/Q1-1` | 0 to 23 | `layout` | `layout`, `cross-shot` |
| `two-shot/Q1-2` | 24 to 47 | `layout` | `layout`, `cross-shot` |
| `lit-render` | 0 to 23, Lumen and TSR on | `render` | `render`, `lighting` |

A unit with `scene.py` alone is not recorded yet.

## Rules

Each `scene.py` holds a `LEVEL` literal equal to its unit's entry of `UNITS` in `lib/golden.py`,
and builds the level from it alone. `tests/test_golden_levels.py` checks every unit without the
engine: the script rules of section 16, read with `ast`; the files a unit may hold; and, once a unit
is recorded, the manifest's shape and hashes and the values the level fixes.

Frames, caches and every engine binary go to the asset store under their sha256, which the recorded
files hold. None of them is in git.

A level changes only through a pull request that an `[ORCHESTRATOR]` comment on it allows, with the new hashes and the reason.

The seeded-defect cases made from these levels are in `corpus/`.
