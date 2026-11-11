# 3D-first pipeline research (engine-neutral, Blender-leaning)

**Evidence basis.** Most claims below come from search-result summaries of the linked pages, not full reads. I read one paper directly (the mesh-conditioned video paper in section 6). Two primary sources I tried to open (the Blender tracker issue and the Golem article) returned 403. Vendor blogs are flagged where they matter.

## 1. Illustration to 3D character

- **Generators.** No source tested anime illustrations specifically.
  - The closest tests (stylised, chibi) split between Hunyuan3D and Tripo ([Ideate](https://ideate.xyz/blogs/posts/ai-3d-model-comparison-trellis-tripo-meshy-rodin-hunyuan), [Zeroatoms](https://zeroatoms.com/blog/ai-3d-model-generators-for-games)).
  - Rodin Gen-2 is credited with the cleanest quads and separable parts ([Falcon](https://falcon.so/resources/best/best-ai-3d-models)).
  - Versions move fast: Meshy 7.1, Tripo v3.1 and Rodin Gen-2.5 are already cited.
- **Anime-specific research.** CharacterGen outputs an A-pose mesh from one image ([repo](https://github.com/zjp-shadow/CharacterGen)). StdGEN splits the character into separable parts ([repo](https://github.com/hyz317/StdGEN)). Both were trained on VRoid Hub data; I did not find their licences.
- **Cost on fal.** TRELLIS is $0.02, Hunyuan3D v3 is $0.225–0.525 and Meshy 6 is $0.80 per generation ([fal pricing](https://fal.ai/pricing)). Rodin at about $0.40 is a third-party figure.
- **Licences.**
  - Hunyuan3D's licence excludes the EU, UK and South Korea, and covers outputs too ([LICENSE](https://huggingface.co/tencent/Hunyuan3D-2.1/blob/main/LICENSE)).
  - Tripo paid plans own outputs only when models are marked private ([Tripo pricing](https://www.tripo3d.ai/product-doc/getting-started/pricing-credits)).
- **Local on a Mac.** A community MPS port of Hunyuan3D 2.1 exists ([repo](https://github.com/VladimirTalyzin/hunyuan3d-2.1-mac-rocm)). Upstream needs about 29 GB for the full pipeline, so check it against the Mac's memory.
- **Rigging.**
  - Mixamo and AccuRIG 2 are free for commercial use but give humanoid body and fingers only ([AccuRIG FAQ](https://actorcore.reallusion.com/learn-and-support/faq/accurig)).
  - Tripo's rig API costs $0.25 per call ([docs](https://developers.tripo3d.ai/en/pricing)). Meshy's API is biped-only ([docs](https://docs.meshy.ai/en/api/rigging)).
  - UniRig's code is MIT, but open issues suggest the anime-trained weights are unreleased, and bones come out unnamed ([repo](https://github.com/VAST-AI-Research/UniRig)).
- **Still human work.**
  - Face topology: vendors themselves concede manual retopology for facial animation ([Tripo](https://www.tripo3d.ai/blog/retopo-an-ai-character-model-for-animation)).
  - Face blendshapes: no auto-rigger produces them.
  - Skirt and hair chains: proximity-based riggers fail here; TailorRig is research only ([write-up](https://alphasignal.ai/news/tailorrig-solves-the-hardest-part-of-rigging-stylized-game-characters)).
  - Toon face shading: hand-edited normals or painted shadow masks remain the taught method ([AnimeShadingPlus manual](https://github.com/EricHu33/AnimeShadingPlus-Anime-Toon-Shader)).
- **My inference on the ceiling.** Generators give a good static sculpt and texture, roughly a blockout or background character. A hero close-up with facial acting needs an artist, or a VRoid Studio base, which permits commercial use ([pixiv](https://vroid.pixiv.help/hc/en-us/articles/4405813333657)).

## 2. Environments

| Option | What you get | Fit for a deterministic pipeline |
|---|---|---|
| World Labs Marble | Splat (.spz/.ply) or GLB mesh, with Blender and Unreal guides; $20–95/month, high-quality mesh on Pro ([pricing](https://marble.worldlabs.ai/pricing)) | Good once exported: it is a frozen asset. Generation itself is not repeatable. |
| HY-World 2.0 | Open weights, splat or mesh output ([repo](https://github.com/Tencent-Hunyuan/HY-World-2.0)) | Same, but Tencent community licence |
| Genie 3 | Video only; no export or API as of the latest reports ([Wikipedia](https://en.wikipedia.org/wiki/Genie_(AI_model))) | Not usable as an asset source |
| Asset kits plus procedural layout | Meshes with known scale and collision | Best fit; splats lack real collision and relighting (my inference) |

## 3. Deterministic render and animation

- **Blender is not bit-reproducible by default.**
  - A tracker issue reports two renders of one scene differing; threading and path guiding are named causes ([#101726](https://projects.blender.org/blender/blender/issues/101726)).
  - CPU, CUDA and OptiX differ even with denoising off ([Blender Artists](https://blenderartists.org/t/different-render-results-from-cuda-optix/1374426)).
  - Design implication (inference): pin the Blender version and device, fix the seed, turn path guiding off, bake every simulation to cache, and gate on a perceptual tolerance, not a hash.
- **Blender on Mac.** Metal Cycles works. A headless startup crash is reported on macOS 27.2 RC ([#166275](https://projects.blender.org/blender/blender/issues/166275)).
- **Unreal.** Repeatability comes from the Niagara determinism toggle, sim caches and warm-up frames ([Epic docs](https://dev.epicgames.com/documentation/unreal-engine/cinematic-rendering-image-quality-settings-in-unreal-engine)).
- **Godot.** `--fixed-fps --write-movie` gives frame-exact timing ([docs](https://docs.godotengine.org/en/stable/tutorials/animation/creating_movies.html)).
- **USD.** It carries skeletons and blendshapes but not control rigs ([UsdSkel](https://openusd.org/dev/api/_usd_skel__intro.html)). I found no round-trip test.
- **Motion without an animator.**
  - Kimodo (NVIDIA, March 2026): its SOMA checkpoints are commercially usable ([repo](https://github.com/nv-tlabs/kimodo)).
  - HY-Motion 1.0: 22 body joints, no fingers, same territorial licence as Hunyuan3D ([card](https://huggingface.co/tencent/HY-Motion-1.0)).
  - Video-to-mocap: GVHMR is open ([paper](https://arxiv.org/pdf/2409.06662)); Rokoko Vision is $0–12/month ([Rokoko](https://www.rokoko.com/products/vision)).
  - Mixamo's clip library is free.
- **Face.**
  - Audio2Face is open (MIT code, NVIDIA Open Model weights) and outputs 52 ARKit blendshapes, with official plugins for Maya and Unreal only ([repo](https://github.com/NVIDIA/Audio2Face-3D)).
  - Rhubarb gives six to nine mouth shapes from a CLI, which suits limited-animation anime mouths ([repo](https://github.com/danielswolf/rhubarb-lip-sync)).

## 4. Agents driving 3D tools

- In BlenderGym, every VLM system trailed human users by a wide margin. The best VLM verifier agreed with humans at 0.66, against 0.79 between humans ([paper](https://arxiv.org/html/2504.01786v1)).
- LL3M admits non-determinism and hallucinated code ([repo](https://github.com/threedle/ll3m)).
- Write-render-inspect loops (VIGA) improve results by 35% or more over one-shot ([paper](https://arxiv.org/pdf/2601.11109)).
- Blender MCP bridges are flaky and execute arbitrary Python ([review](https://hysenlabs.com/projects/ahujasid-blender-mcp)).
- UE 5.8 ships an experimental first-party MCP server ([VP Land](https://www.vp-land.com/stories/unreal-engine-5-8-embeds-an-mcp-server-so-ai-agents-can-drive-the-editor)).
- My inference: agents are reliable at scripted setup, layout, cameras, lighting and batch checks. They are weak at geometry and at judging their own renders. Have them commit `bpy` scripts that run headless, so the script is the reviewable artefact.

## 5. Verification

**Checkable exactly from scene state (my design, standard tooling):**
- Assets: poly count, manifoldness, UV overlap, texel density, bone hierarchy and naming, weight normalisation, height in metres.
- Scene: object transforms, camera intrinsics and framing, light count and energy.
- Motion: the PhysDiff metrics are penetration, float and foot skate, all in millimetres ([paper](https://arxiv.org/pdf/2212.02500)). Add joint-limit violations, velocity and acceleration caps, and free-fall acceleration fitted against 9.81 m/s².

**Identity against the card art:**
- This is a statistical check, not ground truth.
- DINOv2 discriminates better than CLIP, which inflates wrong matches ([VideoCAD ablation](https://arxiv.org/pdf/2505.24838)).
- I found no validated anime identity embedding across illustration and render.
- Use DINO similarity, silhouette IoU and palette delta-E as ratchets against a human-approved turnaround.

**Perceptual units:**

| Metric | What one "noticeable" unit means | Source |
|---|---|---|
| ColorVideoVDP / HDR-VDP-3 | 1 JOD = 75% of observers notice; 10 = identical | [paper](https://arxiv.org/html/2401.11485v1) |
| VMAF | About 6 points = 1 JND; under 2 is noise | [Streaming Learning Center](https://streaminglearningcenter.com/codecs/finding-the-just-noticeable-difference-with-netflix-vmaf.html) |
| Delta-E 2000 | About 1 (the 2.3 figure belongs to the 1976 formula) | [Wikipedia](https://en.wikipedia.org/wiki/Color_difference) |
| FLIP | Per-pixel 0–1 error at a stated pixels-per-degree; no published threshold | [NVIDIA](https://research.nvidia.com/publication/flip) |
| LPIPS | No fixed JND cutoff | [paper](https://arxiv.org/pdf/1801.03924) |

**Video physics benchmarks:**
- They score generated pixels with VLM judges or against real footage. Top models reach about 50 on Physics-IQ Verified; Grok Imagine Video scores 34.8 ([repo](https://github.com/google-deepmind/physics-iq-benchmark)).
- A critique found VideoPhy-2's auto-evaluator barely separates real footage from generated.
- This supports the owner's decision. With 3D you read the state directly and don't need them.

**Proposed definition of the 10% threshold (two gates):**
1. **Physical gate.** Every measured quantity is within 10% of its expected value: character height, walk speed, fall time, fitted gravity. Hard constraints sit alongside: penetration and float at or under 5 mm, zero joint-limit violations.
2. **Perceptual gate.** Each frame is within 0.1 JOD of an approved golden render (ColorVideoVDP at or above 9.9), which is roughly a 0.6 VMAF drop.

**What cannot be measured this way:**
- Whether she reads as Argelia to fans.
- Appeal and acting.
- Fidelity to hand-painted style, because no pixel-aligned reference exists.
- Whether deliberate anime exaggeration is "right".

## 6. Hybrid generative pass

- The paper I read conditions Wan 2.2 on mesh tracking, world position and normals. Against the true render it reaches PSNR 23–25 dB and SSIM about 0.9, and drops to 19 dB on unseen scenes. It has no stylised results ([paper](https://arxiv.org/html/2608.00094)).
- That is far outside a 0.1 JOD gate.
- I found no evidence that a 3D-conditioned pass reaches illustration quality while provably keeping identity and physics. Flat anime fills expose flicker more ([Atlas Cloud](https://www.atlascloud.ai/blog/tips/seedance-2.0-animation-2d), vendor reasoning).
- My inference: after a restyle you can still compare re-estimated depth, pose and flow against the exact render buffers. That beats pure 2D generation, but it is statistical. Keep the restyle as an optional, separately gated stage.

## 7. Honest assessment

- **Better:** repeatable shots, per-asset evals, exact physics and layout checks, regression testing, reuse across episodes.
- **Worse:** time to first shot (weeks, not minutes), the look gap against painted card art, and human artists needed for face, hair, cloth and toon shading. Agents are weak at modelling.
- **Smallest pilot:**
  - One Argelia model: image-to-3D body plus artist or VRoid face.
  - One environment from a kit or a Marble mesh.
  - Three shots: turnaround, walk, one spoken line.
  - All rendered headless from committed scripts, with the asset, physical and golden-frame gates above.
  - A side-by-side human rating against the card art.

## Recommended stack

- Blender (pinned version, CPU Cycles or EEVEE) driven by committed `bpy` scripts.
- Tripo or Rodin for geometry candidates.
- Mixamo or AccuRIG for the body rig, plus hand-made face shapes.
- Kimodo and Mixamo clips for motion.
- Rhubarb for mouths.
- ElevenLabs for audio.
- ColorVideoVDP and FLIP against goldens.
- Nano Banana and Grok for concept art and textures only.

## Main risks and unknowns

- Anime face and toon-shading quality without an artist is unproven.
- Tencent licence territory: I don't know where Primal TCG operates.
- Blender headless stability on current macOS.
- Identity metrics are unvalidated for illustration against render.
- Vendor pricing and versions drift monthly.
- The 0.1 JOD gate only measures drift from a golden that a human has approved.