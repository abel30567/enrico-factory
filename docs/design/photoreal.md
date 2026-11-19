# Photoreal asset path

A production may rule that its look is photoreal: realistic humans (skin that scatters light, wet eyes,
strand hair, cloth), realistic foliage, water and hard-surface materials, under cinematic light. The
image-to-3D path the kit has today (`gen.model3d`, `gen.rig`, one `.glb` per asset into
`kit/engine/build.py`) cannot reach that bar: a generator mesh has baked, single-layer materials, no
skin or eye shading, no strand hair, and the rig is humanoid-only with 24 joints. This document is the
path that replaces it for photoreal productions. The stylised path stays for productions that keep it,
and for blockouts and background props.

## Per asset class

| Class | Path | Scripted | One-off step by a person |
|---|---|---|---|
| Human character | MetaHuman (Unreal Engine 5.8): conform a head mesh to the template, set face coefficients, body constraints, skin and eye parameters, wardrobe slots, then assemble (MetaHuman Creator Python API, runs on Linux) | all of it, inside the editor on the engine node | the owner approves each character's realistic face against its reference art; costume, special hair and props (a braid, an eyepatch) are authored once |
| Head mesh from reference art | photoreal reference picture made from the source art by an image edit (`gen.image`), then a head mesh from it (image-to-3D head) to conform | yes | the owner picks the reference picture |
| Costume | Chaos Outfit Asset (parametric, fits the MetaHuman body), from a store asset or a garment authored once (USD) | fitting and per-shot cloth simulation | authoring a garment the store does not have |
| Creature, vehicle, hero prop | generator mesh as a blockout, then retopology, layered (Substrate) materials, Nanite, and a hand-built rig for a non-humanoid | import, materials, checks | retopology and the non-humanoid rig |
| Environment | scanned assets (Megascans on Fab, Fab Standard License) placed by PCG graphs (`ExecuteGraphInstance` from Python); the Water plugin for sea | placement, generation | claiming the store assets once; the kit stores them by hash in `store.assets` |
| Face performance | MetaHuman Animator offline solve from audio or video (Linux since 5.8, Python) | yes | none |
| Render | Movie Render Graph, Lumen with hardware ray tracing, enough temporal samples for hair; the path tracer only for stills | yes | none |

## What changes in the kit

- An asset may be an engine content pack (`.uasset` files) stored by hash, not only a `.glb`:
  `kit/lib/assets.py`, `kit/lib/glb.py`, `kit/lib/vendor.py`, the import in `kit/engine/build.py`.
- A `metahuman` job form that runs a MetaHuman build script in the editor on the engine node, like a
  scene script, and records the assembled character as an asset.
- `kit/lib/lints/assets.py`: triangle and texture budgets per look (Nanite meshes and 8K skin are in
  budget for photoreal).
- `kit/engine/build.py` and `kit/engine/job.sh`: hardware ray tracing, sample counts and timeouts per
  look. The render node class moves to a 48 GB GPU.
- `kit/resources.json` and `docs/resources.md`: an Epic account login with MetaHuman cloud access
  (texture synthesis and auto-rigging are cloud calls made by the signed-in editor), the Fab store,
  the GPU class.
- Roles: `asset-builder` and `look-reviewer` judge against the production's look bar frames; the
  look-reviewer scores realism (skin, eyes, hair, materials, light), not only identity.

## Order

1. **Proof (orchestrator, on an engine node):** a signed-in headless editor builds one MetaHuman from a
   script, with cloud texture synthesis and auto-rigging, and renders one lit frame. This is the
   unknown everything else depends on. If the cloud steps cannot run headless, the fallback is a
   once-per-character signed-in session that saves the assembled character as a content pack.
2. Content packs as assets (store, lint, import).
3. The `metahuman` job form and its check.
4. Look per production: render settings, budgets, the look-reviewer's realism criteria.
5. Environments by PCG from stored scanned assets.
6. Face performance by MetaHuman Animator.

Steps 2 to 6 are builder tickets with recorded fixtures; only step 1 and the production's own runs use
a real account.

## Risks

- MetaHuman cloud calls from a headless container (step 1).
- A face translated from drawn art to a realistic person may stop reading as the character; the
  owner's approval of each face is the check.
- The Water plugin and Nanite Foliage are experimental in 5.8.
- Fab has no official download API; assets are claimed once by a person and stored by hash.
- Render cost per shot at this bar is unmeasured until step 1.
