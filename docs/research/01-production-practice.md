# How studios divide the work, and what the factory takes from it

Research digest, 2026-11-11. One agent researched how television, feature and animation productions
split the work; a second agent tried to refute each finding by fetching the sources again. What is
below is the corrected version. Claims the second agent could only confirm through search summaries
are marked [summary].

## 1. Studio practice

| Practice | What the sources say | What the factory takes |
|---|---|---|
| **One binding decision-maker** | The showrunner "has the final say in all aspects of the series" (Final Draft). Staff writers "develop and break story"; one writer drafts. | The showrunner is the only role that rules and locks. The owner is the studio: greenlights, money, publishing. |
| **Stage order in writing** | Room breaks the story, beat sheet, single-writer outline, studio and network notes, draft, production draft, revisions, lock [summary]. | The stage ladder: premise, beats, outline, script, each locked before the next. |
| **The writers' room** | Several writers pitch; the showrunner chooses. | A room: several makers on one ticket, one rubric, one selection. |
| **Script coordinator** | Checks each draft for "formatting, spelling, punctuation and continuity", reissues revisions, and compiles the show bible (Wikipedia). It authors the bible, so it is not a pure checker. | Split in two: `canon-keeper` compiles the bible, `canon-reviewer` checks changes to it. |
| **Director inside the showrunner's frame** | In episodic television the producer or showrunner holds final cut; the director is guaranteed a first cut. The two align in a tone meeting (No Film School; DGA Creative Rights Checklist). | Shot design is a maker role (`storyboard-artist`), not the orchestrator. The board ticket carries the showrunner's tone notes. |
| **Script supervisor** | "The editor's and writer's representative on set", keeps continuity logs, tracks the axis and eyelines, and hands paperwork to editorial (Wikipedia). The role sits inside the director's team; it is not independent. | `continuity-supervisor` checks wardrobe, props, facts, axis and eyeline against canon and the episode ledger. Its independence is our addition. |
| **Approve at the cheapest stage** | Storyboard artists "may redraw or re-board a sequence many times before it meets final approval", and "editing the film at the animatic stage prevents the animation of scenes that would be edited out" (Wikipedia, Traditional animation). | Cheap stages first. The animatic is the last gate before render spend. |
| **Correction and on-model roles** | In anime the animation director (sakkan) "corrects key animation drawings and layouts"; the chief animation director keeps a series uniform (Wikipedia, Animation director). | `canon-reviewer` for series uniformity. Correction is the maker relaunched with the notes. |
| **Two-layer QC with severity** | Netflix runs automated inspection before manual QC. Findings are Blocker, Issue or FYI. A promised fix fails the delivery and triggers redelivery and a fix check [summary]. | Deterministic lints first, then a checker's rubric. Notes are `Blocker` or `Issue`; only a Blocker stops a lock. |
| **Picture lock** | A documented sign-off on a specific version of the cut [summary]. | A lock is a merge of a specific commit. |

**Where the factory departs from studios.** "Checkers never author" is not how film or animation
works. The sakkan corrects by redrawing, the script coordinator writes the bible, the script
supervisor is the director's aide, and studio notes are an authority gate rather than verification.
The strict split between maker and checker comes from the software orchestration pattern. It is kept
as a design choice, so that no verdict comes from the agent that wrote the work.

**Not supported by the sources, and dropped.** That a tone meeting gives "scene-by-scene instructions";
that an episode director attends every check; that only new material is re-reviewed after a fix; that
QC operators are themselves audited.

## 2. Story formulas

Starting values. The timing guidance comes from writing about live-action and talking-head video;
none of it is tested on stylised animation.

| Runtime | Formula | Shots |
|---|---|---|
| 15-20 s | hook in the first 1-3 s, then the payoff | 1-2 |
| 30-45 s | kishotenketsu, one act per shot; needs no antagonist | 4 |
| 45-60 s | Dan Harmon's story circle folded into four quadrants | 4 long or 8 short |
| 60-90 s, serial | microdrama: pick up the cliffhanger in about 5 s, one event, one value change, exit on an open question | 5-8 |
| 3-11 min | story circle: eight steps give eight scenes of 2-4 shots | 16-32 |
| 7-11 min | one-paragraph premise, then a twelve-beat outline split 3/6/3 (the Adventure Time method) | |
| 15-20 min | teaser, three or four acts, tag, A and B stories | |
| Season | the hero's journey, with each episode a circle inside it [inference] | |

Plan 6-14 edit shots per finished minute. Modern films average 4.3-4.9 s per shot.

**Continuity documents.**
- **Series bible:** premise, tone, world rules, format, visual guide, canon log.
- **Character bible:** an identity block, hashed references, named wardrobe states, props, voice, explicit negatives.
- **Episode ledger, machine-readable:** per scene, the story day, location, wardrobe state, props and
  their state, and the facts established; plus the state at the end, which seeds the next episode.

The ledger makes plan-level continuity a deterministic check. Whether the picture complies is still judged.

## 3. Checks: deterministic first, judged second

| Check | Level | How | Kind |
|---|---|---|---|
| Stream spec, duration | shot | `ffprobe` | hard gate |
| Black, freeze, silence | shot | `ffmpeg` detectors | hard gate |
| Cut list equals the edit decision list | episode | scene detection, hard cuts only | hard gate |
| Spoken line equals the script | shot | pinned speech recognition; word edits plus protected tokens (names, numbers, negations) | hard gate over a noisy measurement |
| Loudness and true peak | shot, episode | `ebur128` | hard gate |
| Manifest and hashes | all | schema check, the bytes exist in the store | hard gate |
| Plan continuity against the ledger | shot, scene | validator | hard gate on intent, not on pixels |
| Character identity against the sheet | shot | an embedding trained on illustration, calibrated on approved stills first | warning until calibrated |
| Anatomy, hands, props, framing | shot | a vision model with reference images, then a person | judged |
| Action continuity, pacing, story | scene, episode | rubric, owner approval | judged |

Rules that came out of testing an earlier generated-video pipeline:

- **Three outcomes per check:** PASS, FAIL and ERROR. A check that could not run is not a failure and
  must not trigger a paid retry.
- **A model judge is not repeatable.** On three byte-identical clips, a vision-model judge changed its
  verdict on two. So: the model returns only a defect list in a fixed schema, pass or fail is computed
  in code, the model id, prompt hash and rubric version are stored with the verdict, each clip is judged
  three to five times with a vote, and split votes go to a person.
- **A length-relative speech threshold lets dropped words through.** Count word edits and protect names,
  numbers and negations.
- **Keep a labelled golden set** and rerun it whenever the judge changes.

The move to 3D exists to push more checks into the deterministic rows: a scene's state (who is where,
wearing what, holding what, which camera) can be read from the engine instead of judged from pixels.
See `04-3d-pipeline.md` and `05-ue5.md`.

## 4. Published multi-agent film systems

| System | Roles | Reported |
|---|---|---|
| FilmAgent (arXiv 2501.12909) | director, screenwriter, actor, cinematographer; critique-correct-verify on the script, debate-judge on camera | human score 3.98 against 3.04 for its single-agent version and 3.30 for a stronger single-agent baseline. Runs in pre-built 3D spaces; generates no video. |
| MovieAgent (arXiv 2503.07314) | director, screenwriter, storyboard artist, location manager; input is a script and a character bank | gains attributed to hierarchical planning, no reviewer loop |
| AniMaker (arXiv 2506.10540) | director, photography (several candidates per clip, best kept), reviewer (judges each clip with its neighbours), post-production | gains attributed to candidate search and review |
| Cutscene Agent (arXiv 2604.25318) | game-engine cutscenes | criticises earlier systems for emitting JSON or finished video instead of editable assets |

Two of the three attribute their gains to a review loop or to choosing among candidates. That supports
checkers and rooms. None uses GitHub or any durable state a new session could resume from, and none
checks continuity across episodes; both look like gaps in the published work, though the search was
not exhaustive.

## Sources

- https://www.finaldraft.com/blog/simple-guide-to-the-tv-writers-room-hierarchy
- https://en.wikipedia.org/wiki/Script_coordinator
- https://en.wikipedia.org/wiki/Script_supervisor
- https://en.wikipedia.org/wiki/Traditional_animation
- https://en.wikipedia.org/wiki/Animation_director
- https://nofilmschool.com/the-tv-showrunner-glossary
- https://partnerhelp.netflixstudios.com/hc/en-us/articles/115000353211-Introduction-to-Netflix-Quality-Control-QC
- https://deckle.studio/wiki/workflow/organization/dan-harmons-story-circle/
- https://www.skwigly.co.uk/kent-osborne/
- https://en.wikipedia.org/wiki/Microdrama
- https://storyflow.so/blog/what-is-a-production-bible-complete-guide
- https://tech.ebu.ch/docs/r/r128s1v1_0.pdf
- https://www.scenedetect.com/docs/latest/api/detectors.html
- https://arxiv.org/html/2501.12909 (FilmAgent)
- https://arxiv.org/html/2503.07314 (MovieAgent)
- https://arxiv.org/html/2506.10540 (AniMaker)
- https://arxiv.org/html/2604.25318 (Cutscene Agent)
