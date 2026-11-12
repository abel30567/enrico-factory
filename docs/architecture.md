# Architecture

The content factory as diagrams: who is responsible for what, how work moves between rooms, how
GitHub issues and pull requests carry the state, and how the studio improves itself.
`system-brief.md` (v1) is the specification: file formats, the full stage table, exact lock rules.
This file is the picture. Where they disagree, the brief wins. References such as "brief 9" point at
a section of the brief.

In diagrams, markers are written without their square brackets: `LINT PASS` stands for `[LINT] PASS`.

## 1. Two layers

This repository is built with the software orchestration kit (`.agents/orchestrator/`: orchestrator,
builder, verifier). What it builds is a second kit, `kit/`, which runs a show with studio roles. A
show lives in its own production repository.

```mermaid
flowchart LR
  subgraph L1["Layer 1: this repository"]
    ORCH["Orchestrator session"]
    BV["Builder and verifier workers"]
    KIT["kit/<br/>roles, formats, pipeline.json,<br/>lints, evals, factory command line"]
    ORCH -->|"orch launch"| BV
    BV -->|"pull requests, merged by orch merge"| KIT
  end
  subgraph L2["Layer 2: a production repository, one per show"]
    INST[".factory/<br/>the installed kit"]
    RO["Room orchestrators<br/>one per installed room"]
    ROLES["Role workers<br/>makers, checkers, judges,<br/>operators, analysts"]
    WORK["bible, episodes, production, post,<br/>distribution, lab, registries, ledger"]
    RO -->|"factory launch"| ROLES
    ROLES -->|"pull requests, locked by factory lock"| WORK
    INST -.->|"prompts, lint, gate, lock, tick"| RO
  end
  KIT -->|"factory init"| INST
```

The rest of this file describes layer 2.

## 2. System map

Five rooms, each with its own orchestrator. Work moves between rooms only as locked packages. Story
and distribution also exchange two packets as issues.

```mermaid
flowchart LR
  SR["story room<br/>showrunner"]
  LP["production room, Unreal Engine 5<br/>line-producer"]
  PS["post room<br/>post-supervisor"]
  CD["distribution room<br/>channel-desk"]
  LD["lab<br/>lab-director"]
  SR -->|"locked story package:<br/>reel, edit list with beat IDs,<br/>bible version, style lock"| LP
  LP -->|"locked master,<br/>stems, edit list"| PS
  PS -->|"gated packages:<br/>one variant per platform,<br/>each with a file hash"| CD
  SR -.->|"story intent packet"| PS
  SR -.->|"story intent packet"| CD
  CD -.->|"platform notes"| SR
  CD -->|"telemetry snapshots,<br/>symptom notes"| SR
  CD -->|"real outcomes"| LD
  LD -->|"config pull requests,<br/>locked by the receiving room"| LP
  LD --> SR
  LD --> PS
  LD --> CD
```

The producer gate, the state and the media sit under every room.

```mermaid
flowchart TB
  OWNER["Owner<br/>one-time setup, audits afterwards"]
  GATE["Producer: factory gate<br/>caps, ledger, monitors, kill switch"]
  TICK["Timer: factory tick"]
  ROOMS["Five rooms<br/>orchestrator and role workers"]
  STORE[("Media store<br/>the bytes, never in git")]
  subgraph GH["GitHub: the only state"]
    ISS["Issues<br/>tickets, pivot queue, packets"]
    PRS["Pull requests<br/>one artifact each"]
    MK["Comments<br/>first-line markers"]
    MAIN["Base branch<br/>locked work, shared stores,<br/>ledger, media manifest"]
  end
  OWNER -->|"sets the caps once<br/>in production.env"| GATE
  OWNER -.->|"may overrule any ruling afterwards"| MK
  ROOMS -->|"tickets, LAUNCH, RULING"| ISS
  ROOMS -->|"branches and artifacts"| PRS
  ROOMS -->|"READY, verdicts"| MK
  ROOMS -->|"job files for paid or irreversible jobs"| GATE
  GATE -->|"GATE PASS or GATE STOP, by hash"| MK
  GATE -->|"appends ledger/spend.tsv"| MAIN
  TICK -->|"reads state, dispatches the next mechanical step"| ROOMS
  MK -->|"read by factory lock"| MAIN
  ROOMS -->|"frames, caches, assets"| STORE
  STORE -.->|"id, sha256, bytes, source<br/>in media/manifest.tsv"| PRS
```

Five facts hold the system together:

1. **GitHub is the only state.** A new session rebuilds the whole picture from issues, pull requests,
   labels and first-line markers.
2. **A handoff needs a lock.** A room hands on only what it has locked, and an orchestrator locks only
   in its own room.
3. **Every stage pairs a deterministic check with a judged review** by an agent that did not make the
   work.
4. **Spending and anything irreversible pass `factory gate`.** No human approves a step inside the
   loop. The owner sets the caps once and may audit and overrule afterwards.
5. **Engagement can propose but never lock.** What the audience does comes back as notes and pivot
   proposals.

## 3. Responsibilities

### Authority

| Who | Does | May never |
|---|---|---|
| Owner | One-time setup: accounts, logins, platform approvals, the caps, the first charter. Audits afterwards and may overrule any ruling | be asked to approve a step inside the loop |
| Producer (`factory gate`, a program) | State machine, budgets, schedule, kill switch, audit log, the gate on spending and on anything irreversible | make a creative call |
| Room orchestrator | Launches workers, rules after three rounds of notes, locks in its own room. `factory tick` files each next ticket from the pipeline when the stage before it locks (an episode stage after a sequence stage, such as `reel` after `board`, once that stage is locked for every sequence of the episode on the base branch); a person files the first ticket of a line of work | build, check, score, or lock in another room |
| Makers | One artifact per ticket on their own branch, built only from locked inputs | lock, merge, touch another ticket's files |
| Lint (`factory lint`) | Deterministic checks: PASS, FAIL or ERROR | make a judgment call |
| Checkers and judges | Judged review by an agent that did not make the work, from another model family where one is available | fix the work, see the maker's reasoning, lock |
| Operators | Run one paid or irreversible job (render, generate, upload) with one credential | run a job without a producer gate PASS on that exact job |

A role is one file in `kit/roles/`. Adding a department adds files, not code. Correction is the maker
relaunched on the same pull request. There is no fixer role.

### Story room

| Role id | Kind | Owns | Checked by |
|---|---|---|---|
| `showrunner` | orchestrator | vision, pivot triage, note triage, locks, drafting amendments | `devils-advocate` challenges |
| `retro-agent` | maker | season retrospectives, proposed changes to role files and rubrics | `held-out-judge` |
| `researcher` | maker | findings with an evidence table, turned into pivot proposals | `fact-checker` |
| `trend-scout` | analyst | shifts in culture and format | |
| `audience-analyst` | analyst | telemetry mapped onto beats, comment clusters, hypothesis results | |
| `render-reviewer` | checker | defects in the released video | |
| `canon-keeper` | maker | the bible: charter, canon ledger, character and place sheets | `canon-reviewer` |
| `story-writer` | maker | premise, beats, treatments | `story-editor` |
| `script-writer` | maker | outline, script, `lines.json`, the episode ledger | `story-editor` |
| `head-of-story` | maker | sequences, assignments, the continuity ledger, merged notes | `continuity-supervisor` |
| `story-artist` | maker | shot lists and panels for one sequence | `continuity-supervisor`, `pitch-judge` |
| `art-director` | maker | model sheets, color script, the style lock | `look-reviewer` |
| `editorial` | maker | timing, scratch voice, temp score, the reel and its edit list | `story-editor` |
| `fact-checker`, `canon-reviewer`, `story-editor`, `continuity-supervisor`, `look-reviewer` | checker | notes on the exact head | |
| `pitch-judge` | judge | blind per-scene preference, old against new | |
| `braintrust` | judge | whole-episode screenings, symptom notes, votes on amendments (seven seats) | |
| `audience-sim` | judge | persona panel: retell test, emotion curve, predicted drop-offs | |
| `distinctiveness-judge` | judge | penalty for work too close to comparable shows or past episodes | |
| `held-out-judge` | judge | a panel the makers have never been tuned against | |
| `devils-advocate` | checker | challenges to the showrunner's note rejections and pivot calls | |

### Production room

| Role id | Kind | Stage | Judged by |
|---|---|---|---|
| `line-producer` | orchestrator | `intake`, and every production lock | |
| `asset-builder` | maker | `assets` | `look-reviewer` |
| `rigger` | maker | `rigging` | `deformation-reviewer` |
| `layout-artist` | maker | `layout` | `cinematography-reviewer` |
| `animator` | maker | `animation` | `performance-reviewer`, blind against the last take |
| `sim-artist` | maker | `simulation` | lint only |
| `lighter` | maker | `lighting` | `look-reviewer` |
| `render-wrangler` | operator | `render` | `dailies-reviewer` |
| `sound-mixer`, `conform-editor` | maker | `conform` | `mix-reviewer` |
| `continuity-supervisor` | checker | `cross-shot` | |

### Post room

| Role id | Kind | Stage | Judged by |
|---|---|---|---|
| `post-supervisor` | orchestrator | every `variant` lock, and the platform rulebook | |
| `platform-editor`, `reframe-artist`, `caption-writer`, `cta-writer`, `thumbnail-artist` | maker | `variant` | `charter-judge`, `packaging-judge` |
| `charter-judge`, `packaging-judge` | checker | `variant` | |

### Distribution room

| Role id | Kind | Stage | Judged by |
|---|---|---|---|
| `channel-desk` | orchestrator | every distribution lock | |
| `channel-youtube`, `channel-tiktok`, `channel-instagram` | operator | `post` | publish check only |
| `telemetry-puller` | operator | `window` | `theory-judge` |
| `theory-judge` | analyst | `window`, judging the pulled telemetry | |
| `community-analyst` | analyst | | |

### Lab

| Role id | Kind | Works on |
|---|---|---|
| `lab-director` | orchestrator | launching the loops, `lab/results.tsv` |
| `method-researcher` | maker | method research |
| `hypothesis-writer` | maker | hypotheses |
| `experimenter` | maker | one change and its measurement, with the metric, tests, judges and threshold frozen |
| `metric-proposer` | maker | metric amendments |

## 4. Data flow: the stage ladders

A unit of work climbs ordered stages. A stage must be locked before the next stage of the same unit
may start. The full table (unit, maker, lint, judge per stage) is brief 6.

**Story: research to reel.**

```mermaid
flowchart TD
  SRC["sources/"] --> R["research<br/>researcher, unit topic"]
  R -->|"findings and pivot proposals"| B["bible<br/>canon-keeper, unit amendment"]
  B -->|"each episode records its bible version"| P["premise<br/>story-writer, 3 pitches, names its format"]
  P --> BE["beats<br/>story-writer"]
  BE --> OL["outline<br/>script-writer, skipped by short formats"]
  OL --> S["script<br/>script-writer: script.md, lines.json, ledger.json"]
  S --> SQ["sequences<br/>head-of-story"]
  B --> ST["style<br/>art-director, unit style lock"]
  SQ --> BD["board<br/>story-artist, unit sequence"]
  ST --> BD
  BD --> RL["reel<br/>editorial: reel and edit list with beat IDs"]
  RL -->|"showrunner LOCK"| PKG["Locked story package<br/>plus the story intent packet"]
```

**Production: intake to conform, plus cross-shot.**

```mermaid
flowchart TD
  PKG["Locked story package"] --> IN["intake<br/>shot list equals the locked edit list, frame for frame"]
  subgraph ASSET["Per asset"]
    A["assets"] --> RG["rigging"]
  end
  subgraph SHOT["Per shot, on previews"]
    L["layout"] --> AN["animation"]
    AN --> SIM["simulation<br/>locked as a cache"]
    SIM --> LI["lighting"]
  end
  IN --> A
  RG --> L
  LI -->|"layout, animation, simulation<br/>and lighting all locked"| RN["render<br/>gated job, reads caches only"]
  SHOT -->|"shots of one sequence"| XS["cross-shot<br/>world state at the end of shot N<br/>equals the start of shot N+1"]
  RN --> CF["conform<br/>mix, master, frame-exact edit list"]
  XS --> CF
  CF -->|"line-producer LOCK"| M["Locked master, stems, edit list"]
  ASSET -.->|"new version of a locked asset:<br/>each shot using it is stale<br/>at its earliest broken stage"| SHOT
```

**Post and distribution: variant, then post and window.**

```mermaid
flowchart LR
  M["Locked master,<br/>stems, edit list"] --> V["variant<br/>one per platform"]
  IP["Story intent packet"] --> V
  RB["Platform rulebook<br/>one file per platform"] --> V
  V -->|"platform gate PASS on the file hash,<br/>judges approve, post-supervisor LOCK"| GP["Gated packages<br/>registry/variants.tsv"]
  GP --> PO["post<br/>private upload, publish check,<br/>gated public switch"]
  PO --> RP["registry/posts.tsv<br/>live post, hash, beat map"]
  PO --> W["window<br/>telemetry pulls, snapshot"]
  W --> OUT["Telemetry snapshots,<br/>platform notes, symptom notes"]
  OUT -.->|"back to the story room and the lab"| NEXT["Next episode"]
```

Rules that shape the flow:

- **Cheapest first.** No final render until layout, animation, simulation and lighting are locked on
  previews.
- **Caches, not solvers.** A simulation is locked as a cache. Renders only read caches.
- **Stale assets.** A new version of a locked asset marks every shot that uses it stale at that shot's
  earliest broken stage.
- **Media by hash.** Media bytes go to the media store. The pull request commits one manifest row
  (`id`, `sha256`, `bytes`, `source`). A verdict names the head and the manifest hash, so it is a
  verdict on exact bytes.

## 5. One room in detail

Each room has the same five columns: inputs, governance, makers or operators, checks, outputs. Notes
go back to the room's orchestrator. The labelled dashed edge is the room's feedback path.

### Story room

```mermaid
flowchart LR
  SIG["researcher, trend-scout,<br/>audience-analyst, render-reviewer"]
  SIN["Inputs<br/>pivot queue: issues labelled pivot, P0 to P3<br/>platform notes from distribution<br/>notes from analysts and screenings<br/>series bible, playbook"]
  subgraph SGOV["Governance"]
    SR["showrunner<br/>triage, rule, lock"]
    DA["devils-advocate<br/>challenges rejections and pivot calls"]
    SPG["Producer gate<br/>caps"]
  end
  subgraph SMK["Makers"]
    WR["story-writer, script-writer,<br/>head-of-story, canon-keeper"]
    AR["story-artist, art-director,<br/>editorial"]
  end
  subgraph SCH["Checks"]
    SLINT["factory lint"]
    SCHK["fact-checker, canon-reviewer, story-editor,<br/>continuity-supervisor, look-reviewer"]
    SJ["pitch-judge, blind<br/>braintrust, audience-sim,<br/>distinctiveness-judge, held-out-judge"]
  end
  SOUT["Outputs<br/>locked story package<br/>story intent packet"]
  SIG -->|"pivot proposals, symptom notes"| SIN
  SIN --> SR
  DA -.-> SR
  SR -->|"tickets, LAUNCH"| SMK
  SMK -->|"READY"| SCH
  SCH -->|"notes and verdicts"| SR
  SR -->|"LOCK"| SOUT
  SOUT -.->|"released video and telemetry,<br/>through distribution"| SIG
```

### Production room

```mermaid
flowchart LR
  PIN["Inputs<br/>locked story package: reel, edit list,<br/>beat IDs, bible version, style lock<br/>lab KEEPs as config pull requests"]
  subgraph PGOV["Governance"]
    LP["line-producer<br/>shot tickets, rule, lock"]
    PSR["showrunner<br/>story questions only"]
    PPG["Producer gate<br/>render jobs, caps"]
    AREG[("registry/assets.tsv<br/>versions and dependents")]
  end
  subgraph PMK["Makers and operator"]
    AB["asset-builder, rigger,<br/>layout-artist, animator"]
    SL["sim-artist, lighter,<br/>sound-mixer, conform-editor"]
    RW["render-wrangler<br/>operator, gated"]
  end
  subgraph PCH["Checks"]
    PLINT["factory lint<br/>read from the engine and from media"]
    PCHK["look, deformation, cinematography,<br/>performance, dailies and mix reviewers"]
    XS["cross-shot<br/>continuity-supervisor"]
  end
  POUT["Outputs<br/>locked master, stems, edit list<br/>frames and caches in the media store"]
  PIN --> LP
  PSR -.-> LP
  LP -->|"tickets, LAUNCH"| PMK
  PMK -->|"READY"| PCH
  PCH -->|"notes and verdicts"| LP
  LP -->|"LOCK"| POUT
  AREG -.->|"stale shots re-enter at<br/>their earliest broken stage"| PMK
```

The showrunner answers a production question that would change the story, for example cutting a beat
that costs too much to render. The showrunner never locks a shot. The line producer never changes the
story.

### Post room

```mermaid
flowchart LR
  OIN["Inputs<br/>locked master<br/>story intent packet: hook, spoiler list,<br/>CTA prompts, tone"]
  subgraph OGOV["Governance"]
    PS["post-supervisor<br/>assign, rule, lock"]
    OPG["Producer gate"]
    ORB[("Platform rulebook<br/>specs, safe zones, policy")]
    VREG[("registry/variants.tsv<br/>file hash per variant")]
  end
  subgraph OMK["Makers"]
    PE["platform-editor, reframe-artist,<br/>thumbnail-artist"]
    CW["caption-writer, cta-writer"]
  end
  subgraph OCH["Checks"]
    PGATE["Platform gate<br/>duration, aspect, safe zones, captions,<br/>CTA lint, spoiler lint, disclosure flag"]
    OJ["charter-judge, packaging-judge"]
  end
  OOUT["Outputs<br/>gated packages: variants and hashes"]
  OIN --> PS
  ORB --> PGATE
  PS -->|"tickets, LAUNCH"| OMK
  OMK -->|"READY"| OCH
  OCH -->|"PLATFORM-GATE verdict, notes"| PS
  PS -->|"LOCK"| OOUT
  OOUT -.->|"platform notes after release<br/>update the rulebook the next gate reads"| ORB
```

A platform gate verdict names the variant's file hash, so any edit to the file needs new verdicts.

### Distribution room

```mermaid
flowchart LR
  DIN["Inputs<br/>gated packages: variants and hashes<br/>release calendar<br/>story intent packet"]
  subgraph DGOV["Governance"]
    CD["channel-desk<br/>schedule, rule, lock"]
    DPG["Producer gate<br/>upload, public switch"]
    PREG[("registry/posts.tsv<br/>live post, hash, beat map")]
  end
  subgraph DOP["Operators and analysts"]
    CH["channel-youtube, channel-tiktok,<br/>channel-instagram<br/>one credential each"]
    TP["telemetry-puller"]
    CA["community-analyst"]
  end
  subgraph DCH["Checks"]
    PUB["Publish check<br/>exists, processed, hash,<br/>captions, disclosure"]
    TL["Telemetry schema<br/>empty is not zero"]
    TJ["theory-judge"]
  end
  DOUT["Outputs<br/>live posts, telemetry snapshots,<br/>platform notes, symptom notes"]
  DIN --> CD
  CD -->|"tickets, LAUNCH"| DOP
  DOP -->|"POSTED, READY"| DCH
  DCH -->|"verdicts"| CD
  CD -->|"LOCK"| DOUT
  DOUT -.->|"the story room answers with<br/>the next intent packet"| DIN
```

The post registry links every live post to its file hash and beat map. That is how telemetry finds
its way back to story beats.

### Lab

```mermaid
flowchart LR
  LIN["Inputs<br/>results logs, method research,<br/>real outcomes, the defect corpus"]
  subgraph LGOV["Governance"]
    LD["lab-director<br/>launch, rule, log"]
    LPG["Producer gate<br/>compute caps"]
    FRZ["Frozen for the experimenter<br/>metric, experiment harness, tests,<br/>judges, keep threshold"]
  end
  subgraph LMK["Makers"]
    MRS["method-researcher,<br/>hypothesis-writer"]
    EX["experimenter"]
    MP["metric-proposer"]
  end
  subgraph LCH["Checks"]
    GT["Gates<br/>the frozen tests"]
    NT["Noise test<br/>twice the noise, then a re-run"]
    MA["Metric amendment test<br/>predicts real outcomes better<br/>on held-out episodes"]
  end
  LOUT["Outputs<br/>config pull requests into the other rooms<br/>lab/results.tsv, append-only"]
  LIN --> LD
  FRZ -.-> EX
  LD -->|"tickets, LAUNCH"| LMK
  LMK -->|"runs"| LCH
  LCH -->|"KEEP, NOISE, DISCARD, REBASELINE"| LD
  LD --> LOUT
  LOUT -.->|"every run, kept or not,<br/>informs the next hypotheses"| LIN
```

The lab owns the role eval suites in `kit/evals/<role>/`. No role may edit the suite it is measured
by, and a loop never changes its own metric.

## 6. Loops

Each room runs three nested loops. A package leaves a room when the loop that locks it exits; only
the outermost loop changes the room's own rules.

```mermaid
flowchart TB
  subgraph OUTER["Outer loop"]
    subgraph MIDDLE["Middle loop"]
      subgraph INNER["Inner loop"]
        MAKE["Maker or operator works"] --> CHECK["Lint, then checker or judge"]
        CHECK -->|"FAIL or notes"| MAKE
      end
      UNIT["Unit locked"]
      SET["Set of units complete"]
      CHECK -->|"inner exit"| UNIT
      UNIT -->|"next unit"| MAKE
      UNIT -->|"middle exit"| SET
    end
    DONE["Round complete"]
    SET -->|"outer exit"| DONE
  end
  DONE -->|"handoff"| NEXT["Another room"]
  DONE -.->|"results start the next round"| MAKE
```

| Room | Inner loop, exit | Middle loop, exit | Outer loop, exit |
|---|---|---|---|
| story | scene (hours): blind win and lint PASS | episode (days): showrunner lock | season (weeks): retro passes its replay tests. Only this loop amends the bible |
| production | shot (hours): fresh PASS and APPROVED on the head | sequence (days): cross-shot diff clean | episode (week): master matches the locked cut |
| post | variant (minutes): PASS on the file hash, judge approval | package (hours): every variant locked | release window (days): producer gate PASS |
| distribution | post (hours): VERIFIED on the live post | window (7 days): snapshot frozen | season: the story room answers the platform notes |
| lab | experiment (minutes): beats the best by twice the noise, twice | program (days): 5 NOISE in a row, or its cap | metric (season): the new metric predicts outcomes better |

### How work moves: `factory tick`

Orchestrators do not poll. A timer runs `factory tick`. It reads GitHub state, dispatches the next
mechanical step, and wakes an orchestrator only for a judgment. The producer's monitors (brief 11)
run on every tick.

```mermaid
flowchart TD
  T["Timer"] --> TICK["factory tick"]
  TICK --> READ["Read GitHub state<br/>labels, newest markers, heads"]
  READ --> FR{"factory freeze set?"}
  FR -->|"yes"| STOP["No launch, no gate"]
  FR -->|"no"| D{"Next step for each ticket"}
  D -->|"READY or ADDRESSED on a new head"| LN["Run factory lint, post LINT"]
  D -->|"lint passed, verdict missing"| CK["Launch the named checker or judge"]
  D -->|"notes or lint FAIL"| MK["Relaunch the maker on the same pull request"]
  D -->|"job file waiting"| GT["Run factory gate"]
  D -->|"every lock rule met"| LK["factory lock<br/>at most one ticket per room"]
  D -->|"a judgment is needed"| WK["Wake the room's orchestrator<br/>ruling after three rounds, pitch selection,<br/>pivot triage, BLOCKED worker"]
```

## 7. How GitHub is used

### Objects

| GitHub object | Means | Written by |
|---|---|---|
| Issue `[KEY] summary`, labels `tracked`, `room:<room>`, `stage:<stage>`, `unit:<unit-id>` | a ticket: one unit at one stage in one room | `factory tick`, when the stage before it locks (an episode stage after a sequence stage: once it is locked for every sequence of the episode on the base branch); a person for the first ticket of a line of work |
| Issue labelled `pivot`, class `P0`..`P3` | one entry in the pivot queue: what it touches and a hypothesis written before testing | researcher, trend-scout, audience-analyst |
| Issue: story intent packet | hook, spoiler list, CTA prompts, tone, with every locked episode | story room |
| Issue: platform notes | format limits, CTA results, audience theories, after every release | distribution room |
| Branch and pull request | one artifact. One per ticket, except a pitched stage, where each pitch has its own | maker or operator |
| Comment, first line | a marker (brief 8). Only the first line counts | the role or command the marker names |
| Merge to the base branch | the lock. Refused while the pull request carries `hold` | `factory lock --apply` only |
| Files on the base branch | locked work and the shared stores (brief 7) | by pull request |

A verdict counts only if it ends with `reviewed head <sha> manifest <hash>` naming the current head
and manifest hash, is newer than the maker's last `READY` or `ADDRESSED`, and comes from the role the
pipeline names for that stage. `ERROR` means the check could not run. It is never read as PASS or FAIL.

### A ticket's life

Each state is derived from labels and the newest markers. Nobody sets it by hand. The state names
below are descriptive. The brief fixes the markers, not the names.

```mermaid
stateDiagram-v2
  state "Lint failed" as LintFailed
  state "In review" as InReview
  state "Notes given" as Notes
  [*] --> Filed: factory tick files the ticket
  Filed --> Launched: LAUNCH maker
  Launched --> Ready: maker posts READY
  Launched --> Blocked: maker posts BLOCKED
  Blocked --> Launched: RULING, then a new LAUNCH
  Ready --> LintFailed: LINT FAIL
  Ready --> InReview: LINT PASS
  LintFailed --> Ready: maker relaunched, posts ADDRESSED
  InReview --> Notes: NOTES with a Blocker, or the old version preferred
  Notes --> Ready: maker relaunched, posts ADDRESSED
  InReview --> Approved: every named checker APPROVED, blind win where required
  Approved --> Locked: factory lock merges and posts LOCK
  Locked --> Stale: a locked asset it uses gets a new version
  Stale --> Filed: work restarts at the earliest broken stage
  Locked --> [*]
  note right of Notes
    After three rounds of notes
    the room's orchestrator posts RULING
  end note
```

### One ticket, LAUNCH to LOCK

A `beats` ticket in the story room. Every room follows the same steps with its own roles.

```mermaid
sequenceDiagram
  participant SR as showrunner
  participant FC as factory tick, lint, lock
  participant GH as GitHub
  participant MK as story-writer
  participant CK as story-editor
  FC->>GH: ticket BEATS-EP01, labels tracked, room story, stage beats, unit ep01, filed when the premise locks
  SR->>GH: SHOWRUNNER LAUNCH story-writer, with prompt, sha256, model, harness version, base commit
  SR->>MK: factory launch, a worktree and the rendered prompt
  MK->>GH: branch, pull request, STORY-WRITER READY with head SHA and lint table
  FC->>GH: LINT PASS, reviewed head and manifest
  FC->>CK: launch the named checker in a worktree at the head
  CK->>GH: STORY-EDITOR NOTES, B1 Blocker, I1 Issue
  FC->>MK: relaunch on the same pull request
  MK->>GH: new commits, STORY-WRITER ADDRESSED, per note a change or a rebuttal
  FC->>GH: LINT PASS on the new head
  FC->>CK: launch on the new head
  CK->>GH: STORY-EDITOR APPROVED, reviewed head and manifest
  FC->>GH: factory lock checks the ten rules of brief 9, merges, posts SHOWRUNNER LOCK
```

### A pitched ticket

`pitches: N` in the pipeline launches N makers on one ticket. `premise` uses three.

```mermaid
sequenceDiagram
  participant SR as showrunner
  participant FC as factory tick, lint, lock
  participant GH as GitHub
  participant WR as three story-writers
  participant ED as story-editor
  SR->>GH: one premise ticket for the episode
  par three pitches, three branches
    SR->>WR: factory launch on the same ticket, same locked inputs
    WR->>GH: three pull requests, each with READY
  end
  FC->>GH: LINT on each pull request
  FC->>ED: launch the named checker
  ED->>GH: all three scored on one rubric, NOTES or APPROVED on each
  FC->>SR: wake for a pitch selection
  SR->>GH: SHOWRUNNER SELECTED with the pull request number and the reason
  FC->>GH: factory lock merges the selected pull request only
```

### A bible amendment

The charter and canon change only by amendment. A charter or arc change also needs a Braintrust vote,
and a P2 or P3 pivot needs a supermajority, 5 of 7. The tally below is an invented example.

```mermaid
sequenceDiagram
  participant SR as showrunner
  participant CK as canon-keeper
  participant FC as factory lint, lock
  participant GH as GitHub
  participant CR as canon-reviewer
  participant BT as braintrust
  SR->>GH: bible ticket, unit amendment, the drafted change and its evidence
  SR->>CK: LAUNCH canon-keeper
  CK->>GH: pull request on bible files, with bible/amendments/A-n.json, CANON-KEEPER READY
  FC->>GH: LINT PASS for provenance, references, canon contradictions
  CR->>GH: CANON-REVIEWER APPROVED, reviewed head and manifest
  opt charter or arc change
    BT->>GH: BRAINTRUST VOTE 5-2, the tally and each seat's reason
  end
  FC->>GH: factory lock checks rule 6, merges, posts SHOWRUNNER LOCK
  Note over GH: Later episodes record the new bible version. Released episodes are never edited.
```

A pull request that touches `bible/**` needs its amendment, the vote its class requires and
`canon-reviewer` `APPROVED`, whatever its stage. A writer cannot change canon inside a script.

### A gated job

`factory gate` is the only path to spending money or doing something that cannot be undone. The
example is a final render.

```mermaid
sequenceDiagram
  participant OP as render-wrangler
  participant GH as GitHub
  participant GT as factory gate
  participant LG as ledger
  participant RN as factory run
  participant TL as render nodes
  OP->>GH: job file with inputs by hash, tool, expected cost
  GT->>GH: read the verdicts the job depends on
  GT->>LG: read spend against the caps in production.env
  alt inside every cap, verdicts count
    GT->>LG: append the ledger line
    GT->>GH: PRODUCER GATE PASS on the job hash, the cap and the ledger line
    RN->>TL: run the job with the credential, bytes go to the media store
    RN->>LG: actual cost
    RN->>OP: outputs and manifest rows
    OP->>GH: RENDER-WRANGLER READY, posted for it by the launcher
  else a cap tripped
    GT->>GH: PRODUCER GATE STOP with the cap that tripped
  end
```

### The staged release

A public post cannot be taken back, so release has three steps. The public switch is its own gated
job. The operator's markers are `[CHANNEL:<PLATFORM>] POSTED` and `VERIFIED`.

```mermaid
sequenceDiagram
  participant CD as channel-desk
  participant OP as channel-youtube
  participant FC as factory lint, gate
  participant PF as platform
  participant GH as GitHub
  CD->>OP: LAUNCH on a post ticket for one gated variant
  OP->>GH: job file for the upload, naming the variant file hash
  FC->>GH: PRODUCER GATE PASS on the upload job
  OP->>PF: private upload
  OP->>GH: POSTED with post id, file hash, visibility private
  PF-->>OP: processing finished
  FC->>GH: LINT PASS for the publish check, exists, processed, hash, captions, disclosure
  OP->>GH: VERIFIED on the live post
  OP->>GH: job file for the scheduled public switch
  FC->>GH: PRODUCER GATE PASS on the switch job
  OP->>PF: switch to public on schedule
  CD->>GH: registry/posts.tsv row on lock, with live post, hash, beat map
```

## 8. The lab

Each loop changes one thing, measures one number, keeps the change only if it beats the best result
by more than the noise, and logs every run. The five loops and their metrics are in brief 13.

```mermaid
flowchart TD
  CTRL["Before the loop starts<br/>5 to 10 no-op controls,<br/>keep threshold at twice their spread or more"]
  H["Hypothesis"] --> C["One change"]
  C --> G["Gates<br/>the frozen tests"]
  G --> M["Measure the loop's one metric"]
  M --> N{"Beats the best result<br/>by more than the threshold?"}
  N -->|"yes"| RR{"Re-run once.<br/>Still beats it?"}
  RR -->|"yes"| KEEP["AUTORESEARCH KEEP"]
  RR -->|"no"| NOISE["AUTORESEARCH NOISE"]
  N -->|"inside the noise"| NOISE
  N -->|"worse"| DISCARD["AUTORESEARCH DISCARD"]
  KEEP --> LOG["Append the run to lab/results.tsv"]
  NOISE --> LOG
  DISCARD --> LOG
  LOG -->|"next run"| H
  CTRL --> H
  DRIFT["Every 10 to 15 runs<br/>a no-op control"] -->|"drift past the threshold"| RB["AUTORESEARCH REBASELINE"]
  RB --> LOG
```

A program stops after 5 NOISE results in a row, or at its cap. Live platform loops only confirm what
offline loops already found.

### How a KEEP reaches a room

The lab never changes a room directly. A winning change is a config pull request that the room's own
orchestrator locks after that room's checks. The example is the render cost loop.

```mermaid
sequenceDiagram
  participant LD as lab-director
  participant EX as experimenter
  participant GH as GitHub
  participant PC as production lint and checkers
  participant LP as line-producer
  LD->>EX: LAUNCH on the render cost loop
  EX->>GH: one change, measured, then re-run once
  LD->>GH: AUTORESEARCH KEEP with metric, runs, noise floor, the results-log line
  EX->>GH: config pull request into the production room
  PC->>GH: LINT PASS and APPROVED on that head
  LP->>GH: factory lock in the production room, LINE-PRODUCER LOCK
  Note over LD,LP: The lab never locks in another room.
```

## 9. Compared with the software orchestration kit

Most parts carry over from the kit that builds this repository. What changes: artifacts are media,
half of every check is a judgment about how something looks or plays, and no person approves.

| Software kit | Content factory | What changed |
|---|---|---|
| Orchestrator, the only role that merges | Room orchestrator: `showrunner`, `line-producer`, `post-supervisor`, `channel-desk`, `lab-director` | five instead of one. Each locks only in its own room |
| Owner decisions, orchestrator-only resources | Producer gate: `factory gate`, a program | caps set once in `production.env`. An operator gets its credential only after `[PRODUCER] GATE PASS` on the exact job |
| Builder | Maker, one role file per craft. Operator for gated jobs | roles are data files. Media is delivered as bytes in the store plus a manifest row |
| Verifier | `factory lint` plus the stage's named checker or judge | deterministic and judged results are separate verdicts. A revision must win blind against the locked version |
| Fixer | the maker relaunched on the same pull request | no separate role |
| Blast radius | the stale-asset rule, and `canon-reviewer` on any change to `bible/**` | a new version of a locked asset marks dependent shots stale at their earliest broken stage |
| CI gates (`GATES`) | `factory lint` | PASS, FAIL or ERROR. ERROR is never read as either |
| Merge gate, `orch merge --apply`, one merge per run | `factory lock --apply`, one pull request per run | adds: upstream stage locked, blind win, pitch selected, amendment and vote, `writes` globs and room, twice-raised notes acted on, producer gate |
| `[BUILDER] READY` / `ADDRESSED` / `BLOCKED` | `[<ROLE>] READY` / `ADDRESSED` / `BLOCKED` | role id in the marker |
| `[VERIFIER] CHANGES REQUESTED` / `VERIFIED` | `[<ROLE>] NOTES` / `APPROVED`, `[LINT]`, `PREFERS A` / `PREFERS B` | a note is a Blocker or an Issue. Only a Blocker stops a lock |
| `[ORCHESTRATOR]` launch records and rulings | `[<ORCHESTRATOR>] LAUNCH`, `RULING`, `SELECTED`, `LOCK` | one per room. `LOCK` is posted by `factory lock` |
| Verdict names the head commit, survives a base-only merge | verdict names the current head and the manifest hash | the hash covers media bytes. Stricter |
| Issue `[KEY]` with `tracked` | the same, plus `room:`, `stage:` and `unit:` | the lock needs the room, unit and stage |
| One task, one pull request | the same, except a pitched stage | one ticket, several pull requests, one selected |
| The orchestrator session drives every step | `factory tick` on a timer | mechanical steps need no model. The orchestrator wakes only for a judgment |

## 10. Runtime

How one role runs, and what it can reach. `harness.md` is the specification; `resources.md` lists
every resource by name, with what exists and what is missing.

```mermaid
flowchart LR
  subgraph PROG["Programs: hold every credential"]
    TICK["factory tick<br/>on a timer"]
    LAUNCH["factory launch<br/>worktree, prompt, LAUNCH marker,<br/>commit, pull request, markers"]
    GATE["factory gate<br/>caps and ledger"]
    RUN["factory run<br/>one gated call"]
    GW["Knowledge gateway<br/>only the role's capabilities,<br/>tagged by production"]
  end
  subgraph BOX["Sandbox: one role, one worktree"]
    H["Agent harness (hbar)<br/>headless, tools from the role file,<br/>no GitHub credential, no keys"]
  end
  subgraph LOGIN["Inference: subscription logins"]
    POOL["Account pool<br/>token on a file descriptor"]
    PROXY["Local proxy<br/>second model family"]
  end
  FERMI["Fermi MCP<br/>skills, memory, search"]
  GH["GitHub<br/>issues, pull requests, markers"]
  subgraph KEYS["Tools: keyed, brokered"]
    GEN["Image, video and voice generation"]
    RENDER["Render nodes<br/>Unreal Engine, headless"]
    STORE["Media store"]
    PLAT["YouTube, TikTok, Instagram"]
  end
  TICK --> LAUNCH
  LAUNCH -->|"starts"| H
  H -->|"result object"| LAUNCH
  LAUNCH --> GH
  POOL -.-> H
  PROXY -.-> H
  H --> GW --> FERMI
  H -->|"job file"| GATE
  GATE -->|"GATE PASS"| RUN
  RUN --> GEN
  RUN --> RENDER
  RUN --> STORE
  RUN --> PLAT
  GATE --> GH
```

| Layer | Rule | Why |
|---|---|---|
| Harness | one, used as a primitive through its public flags | a role's behaviour is changed in its file, tools and knowledge, not in the harness |
| Inference | subscription logins only, passed out of band | no inference key exists to leak, and cost is a known flat amount |
| Tools | reached by key, by a program, after a gate PASS | an agent cannot spend or publish by itself |
| Knowledge | Fermi, through a gateway that exposes capabilities | a memory advises; only a locked file binds |
| State | GitHub, written only by programs | a role cannot post another role's verdict |
| Fork | only when a role's evals fail three lab programs in a row for a cause in the harness | the fork serves that process only and has its own engineering team |

## 11. What is not built

Everything in this file is design. Nothing is built or measured yet. The kit is built in this
repository in the waves of brief 17: text-stage machinery and lints first, then the command line and
the story room, then production tooling, a one-shot pilot, a full episode, and the live loops last.
