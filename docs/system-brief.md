# Content factory: system brief (v1)

Specification, 2026-11-11. This is what the repository builds. It adopts the Autonomous Story Studio
proposal (`docs/design/autonomous-story-studio.html`) and replaces brief v0. Companion files:

- `docs/architecture.md`: the same system as diagrams.
- `docs/harness.md`: how a role runs on the agent harness, and when a harness is forked.
- `docs/resources.md`: every resource a run needs and how an unattended agent reaches it.
- `docs/research/`: the research behind it.

This brief wins where they disagree. Everything here is design: nothing is built or measured yet.

## 1. What it is

A studio run entirely by agents, built for an ongoing show. Five rooms each have their own
orchestrator: story, production (Unreal Engine), post, distribution, and an autoresearch lab. Work
moves between rooms only as locked packages. Every stage pairs a deterministic check with a judged
review. What the audience does feeds the next episode and the studio's own improvement loops.
GitHub is the only state. No human approves anything inside the loop; a person can audit any run
afterwards.

It is project-agnostic. A show lives in its own production repository; this repository builds the kit
that is installed into it. The first sample production is described in `samples/primal/README.md`.

What changed from v0:

| v0 | v1 |
|---|---|
| One showrunner orchestrates everything | Five rooms, one orchestrator each; a producer enforces caps across all of them |
| Owner greenlights at premise, board and publish | No approval inside the loop. The producer's gate stands in for the owner |
| "Room" meant several makers pitching one ticket | A room is an orchestrated department. Parallel pitches are a stage option (`pitches`) |
| Paid jobs run only in the showrunner's session | Operator roles request them; a program runs each one, only after a producer gate PASS on the exact job |
| Blender pilot, Unreal as a side test | Unreal Engine 5 is the production engine |
| Story only | Story, production, post, distribution, lab |

## 2. Rules of power

With no human director, these rules take the place of the director's authority. They are pasted into
every role prompt, and the gates in sections 9 and 11 enforce the ones a program can check.

1. **No agent approves its own work.** Makers make, judges score, the room's orchestrator decides, the producer enforces.
2. **The charter changes only by amendment.** The showrunner proposes, the Braintrust votes, and the amendment is logged with its evidence.
3. **Engagement can propose but never lock.** Metrics produce notes and pivot proposals. They never approve a scene.
4. **A revision must beat the old version blind.** Judges compare old and new without knowing which is which. Absolute scores are never enough.
5. **A note raised twice cannot be rejected.** The same problem at two screenings in a row must be acted on.
6. **Released canon is never edited.** A mistake in a released episode is fixed in a later episode.
7. **The producer's caps always win.** Cost, time and iteration limits override every other agent.

### Authority

| Who | Does | May never |
|---|---|---|
| Owner | One-time setup: accounts, logins, platform approvals, the caps the producer enforces, the first charter. Audits afterwards and may overrule any ruling | be asked to approve a step inside the loop |
| Producer | State machine, budgets, schedule, kill switch, audit log, the gate on spending and on anything irreversible | make a creative call |
| Room orchestrator | Launches workers, rules after three rounds of notes, locks in its own room. `factory tick` files each next ticket from the pipeline when the stage before it locks; a person files the first ticket of a line of work | build, check, score, or lock in another room |
| Makers | One artifact per ticket on their own branch, built only from locked inputs | lock, merge, touch another ticket's files |
| Lint | Deterministic checks: PASS, FAIL or ERROR | make a judgment call |
| Checkers and judges | Judged review by an agent that did not make the work, from another model family where one is available | fix the work, see the maker's reasoning, lock |
| Operators | Request one paid or irreversible job (render, generate, upload) and check what it produced | have a job run without a producer gate PASS on that exact job; hold its credential |

The producer is a program, not a model: `factory gate` applies the caps in `production.env` and the
rules in section 11 to the ledger and the markers. It has no opinion to argue with.

## 3. Rooms

| Room | Orchestrator | Takes in | Locks and hands on |
|---|---|---|---|
| `story` | `showrunner` | series bible, pivot queue, notes, platform notes | locked story package: reel, edit list with beat IDs, bible version, style lock, story intent packet |
| `production` | `line-producer` | locked story package, lab KEEPs | locked master, stems, edit list |
| `post` | `post-supervisor` | locked master, story intent packet, platform rulebook | gated packages: one variant per platform, each with a file hash |
| `distribution` | `channel-desk` | gated packages, release calendar | live posts, telemetry snapshots, platform notes, symptom notes |
| `lab` | `lab-director` | results logs, method research, real outcomes, the defect corpus | config pull requests into the other rooms |

Rules between rooms:

- A handoff happens only when the sending room has locked the package.
- An orchestrator locks only in its own room. The showrunner answers story questions from production
  (for example cutting a beat that costs too much to render) but never locks a shot; the line producer
  never changes the story.
- The lab never changes a room directly. A winning change is a pull request that the room's own
  orchestrator locks after that room's checks.
- Story and distribution exchange two packets as issues: the **story intent packet** (hook, spoiler
  list, CTA prompts, tone) with every locked episode, and **platform notes** (format limits, CTA
  results, audience theories) after every release. Neither side edits the other's work.

## 4. Roles

A role is one file in `kit/roles/`. Adding a department adds files, not code. A role with
`room: studio` serves every room (`look-reviewer`, `continuity-supervisor`) and is launched by the
orchestrator of the room whose ticket it works on. Kinds: `orchestrator`,
`maker`, `checker` (judged, gives notes), `judge` (scores or prefers, blind), `operator` (requests gated
jobs), `analyst` (turns data into notes and proposals).

**Story room** (built first):

| Role id | Kind | Owns | May not | Checked by |
|---|---|---|---|---|
| `showrunner` | orchestrator | vision, pivot triage, note triage, locks, drafting amendments | score work; edit the charter without a vote; reject a note raised twice | `devils-advocate` challenges |
| `retro-agent` | maker | season retrospectives; proposed changes to role files and rubrics | ship a change that has not passed the replay suite | `held-out-judge` |
| `researcher` | maker | findings in the show's subject, with an evidence table, turned into pivot proposals | write story; skip the source and evidence fields | `fact-checker` |
| `trend-scout` | analyst | shifts in culture and format | propose canon changes | |
| `audience-analyst` | analyst | telemetry mapped onto beats, comment clusters, hypothesis results | prescribe fixes (symptoms only) | |
| `render-reviewer` | checker | defects in the released video: continuity, identity drift, lip sync | change released video | |
| `canon-keeper` | maker | the bible: charter, canon ledger, character and place sheets | change canon outside an amendment | `canon-reviewer` |
| `story-writer` | maker | premise, beats, treatments | contradict the canon ledger | `story-editor` |
| `script-writer` | maker | outline, script, `lines.json`, the episode ledger | contradict the canon ledger | `story-editor` |
| `head-of-story` | maker | splitting a script into sequences, assignments, the continuity ledger, merged notes | lock scenes | `continuity-supervisor` |
| `story-artist` | maker | shot lists and panels for one sequence | change the style lock | `continuity-supervisor`, `pitch-judge` |
| `art-director` | maker | model sheets, color script, the style lock | restyle mid-episode without an amendment | `look-reviewer` |
| `editorial` | maker | timing, scratch voice, temp score, the reel and its edit list | drop a beat silently | `story-editor` |
| `fact-checker`, `canon-reviewer`, `story-editor`, `continuity-supervisor`, `look-reviewer` | checker | notes on the exact head | fix the work | |
| `pitch-judge` | judge | blind per-scene preference, old against new | see makers' reasoning | |
| `braintrust` | judge | whole-episode screenings, symptom notes, votes on amendments (seven seats) | prescribe fixes; lock | |
| `audience-sim` | judge | persona panel: retell test, emotion curve, predicted drop-offs | override real telemetry | |
| `distinctiveness-judge` | judge | penalty for work too close to comparable shows or to past episodes | veto alone | |
| `held-out-judge` | judge | a panel the makers have never been tuned against | leak its prompts | |
| `devils-advocate` | checker | challenges to the showrunner's note rejections and pivot calls | decide | |

**Production room:** `line-producer`; makers `asset-builder`, `rigger`, `layout-artist`, `animator`,
`sim-artist`, `lighter`, `sound-mixer`, `conform-editor`; operator `render-wrangler`; checkers
`look-reviewer`, `deformation-reviewer`, `cinematography-reviewer`, `performance-reviewer`,
`dailies-reviewer`, `mix-reviewer`, `continuity-supervisor`.

**Post room:** `post-supervisor`; makers `platform-editor`, `reframe-artist`, `caption-writer`,
`cta-writer`, `thumbnail-artist`; checkers `charter-judge`, `packaging-judge`.

**Distribution room:** `channel-desk`; operators `channel-youtube`, `channel-tiktok`,
`channel-instagram`, `telemetry-puller`; analysts `community-analyst`, `theory-judge`.

**Lab:** `lab-director`; makers `hypothesis-writer`, `experimenter`, `metric-proposer`, `method-researcher`.

**Rights.** No role is added for rights (section 16, "Rights"). `researcher` writes the names it
meets that belong to someone else into `research/not-ours.tsv`. `canon-reviewer` raises a Blocker on a
new character, place or creature that has no origin or that takes after someone else's; `look-reviewer`
raises one on a design that looks like someone else's character; `packaging-judge` raises one on a
title, a caption or a thumbnail that uses one. The room's orchestrator may not rule such a Blocker
away. No role writes `sources/**`.

**Trends.** `researcher` writes the topics the production watches on each platform and judges whether
a trend is usable; `trend-scout` reads the same data (section 16, "Other people's videos"). Neither
holds a platform key: a program makes the reads. A trend reaches the show only as a pivot proposal,
which the showrunner triages like any other.

Correction is the maker relaunched on the same pull request to address the notes. There is no fixer role.

## 5. Role file format

`kit/roles/<role-id>.md`: front matter of plain `key: value` lines between `---` lines, then the
prompt body. No YAML library; values are strings, and list values are comma-separated.

```
---
role: story-artist
room: story                # story | production | post | distribution | lab | studio
kind: maker                # orchestrator | maker | checker | judge | operator | analyst
analog: story artist
stage: board               # stages this role works in
writes: episodes/*/sequences/*/shots/**
reads: bible/**, episodes/**
checked_by: continuity-supervisor, pitch-judge   # makers only
may_not: change the style lock
model: maker               # maker | checker: which configured model slot runs it
family: any                # any | other: `other` must not share the maker's model family
tools: Read, Edit, Write, Grep, Glob, Bash       # harness tools the role may use
knowledge: skills, memory-read                   # Fermi capabilities (docs/harness.md); empty for none
secrets:                   # names of tool credentials injected into this role's environment
gated: no                  # yes for operators: each job needs a producer gate PASS
---
<prompt body>
```

The prompt body uses `{{NAME}}` placeholders. Required in every body: `{{REPO}}`, `{{TICKET}}`,
`{{WORKTREE}}`, `{{TASK}}`, `{{RULES}}` (the rules of power plus the production's rules file). Makers
also get `{{BRANCH}}`; checkers and judges also get `{{PR}}`. Every body states the read-first list,
the role's `may_not`, the exact markers it may post, the provenance rule and the BLOCKED protocol.
Checker and judge bodies contain their rubric. A judge body never receives the maker's comments.

## 6. Stages

`kit/pipeline.json` is the default ladder; a production may override it. It holds the stages of the
rooms built so far: each room adds its entries together with its lints. One entry per stage:

```json
{"room": "story", "stage": "board", "unit": "sequence", "after": "sequences",
 "maker": "story-artist", "lint": ["structure", "provenance", "references", "media"],
 "checker": "continuity-supervisor", "judge": "pitch-judge", "blind": true,
 "pitches": 1, "paths": ["episodes/*/sequences/*/shots/**"]}
```

Stages run in table order; `after` names the stage that must be locked first. Two exceptions: `style`
follows `bible` and must be locked before `board`, and `cross-shot` runs once every shot of the
sequence has locked `lighting`, before any of them renders. `maker`, `checker` and `judge` are each a
role id, a list, or empty.

`blind: true` means a revision of an already locked unit needs a blind win over the locked version
(rule 4). `pitches: N` launches N makers on one ticket; the judge scores all of them on one rubric and
the orchestrator selects one.

| Room | Stage | Unit | Maker | Lint | Judged by |
|---|---|---|---|---|---|
| story | `research` | topic | researcher | provenance, references | fact-checker |
| story | `bible` | amendment | canon-keeper | provenance, references, canon contradictions | canon-reviewer; a Braintrust vote for charter and arc changes |
| story | `premise` | episode | story-writer, 3 pitches | format named and known | story-editor |
| story | `beats` | episode | story-writer | format's beats in order, durations inside the runtime | story-editor |
| story | `outline` | episode | script-writer | every beat covered by a scene | story-editor |
| story | `script` | episode | script-writer | every line in `lines.json`, ledger complete | story-editor |
| story | `sequences` | episode | head-of-story | every scene assigned to one sequence | continuity-supervisor |
| story | `style` | style lock | art-director | references resolve, palette values present | look-reviewer |
| story | `board` | sequence | story-artist | shot schema, durations, ids exist in the bible | continuity-supervisor, pitch-judge (blind) |
| story | `reel` | episode | editorial | edit list covers every beat ID, runtime | story-editor, then Braintrust, audience-sim, distinctiveness-judge, held-out-judge |
| production | `intake` | episode | none: `factory` writes the shot tickets from the locked edit list | shot list equals the locked edit list frame for frame | none |
| production | `assets` | asset | asset-builder | naming, references, budgets, scale against the bible, skeleton | look-reviewer |
| production | `rigging` | asset | rigger | range of motion, limits, interpenetration, rig hash | deformation-reviewer |
| production | `layout` | shot | layout-artist | focal length, framing, duration, 180° line, 9:16 safe zones | cinematography-reviewer |
| production | `animation` | shot | animator | foot slide, pops, interpenetration, lip sync within one frame | performance-reviewer (blind against the last take) |
| production | `simulation` | shot | sim-artist | run twice, cache hashes match; velocity and energy in bounds | none: deterministic only |
| production | `lighting` | shot | lighter | exposure and color against the sequence key, clipping, time of day | look-reviewer |
| production | `render` | shot | render-wrangler | frame count, no black or NaN frames, no engine text (OCR), sampled re-render inside tolerance | dailies-reviewer |
| production | `conform` | episode | sound-mixer, conform-editor | loudness, true peak, captions aligned, edit list frame-exact | mix-reviewer |
| production | `cross-shot` | sequence | none | world state at the end of shot N equals the start of shot N+1 | continuity-supervisor |
| post | `variant` | variant | platform-editor, reframe-artist, caption-writer, cta-writer, thumbnail-artist | platform gate: duration, aspect, safe zones, captions, CTA lint, spoiler lint, disclosure flag | charter-judge, packaging-judge |
| distribution | `post` | post | channel operator | publish check: exists, processed, hash, captions, disclosure | none |
| distribution | `window` | post | telemetry-puller | telemetry schema; empty is not zero | theory-judge |

Production stages lock cheapest first: no final render until layout, animation, simulation and
lighting are locked on previews. A simulation is locked as a cache; renders only read caches. A new
version of a locked asset marks every shot that uses it stale at that shot's earliest broken stage.

A ticket is a GitHub issue titled `[KEY] summary` with labels `tracked`, `room:<room>`,
`stage:<stage>` and `unit:<unit-id>`. One ticket, one maker, one branch, one pull request, except a
pitched stage, where each pitch has its own pull request.

### Formats

A format is the story formula for a runtime: one file in `kit/formats/`, same front matter rules as a
role file, then guidance for the writer and the story editor.

```
---
format: short-four-act
runtime: 30-45             # seconds, minimum-maximum
beats: introduction, development, twist, conclusion
shots: 4-8
skips: outline             # stages this format does not use; empty for none
---
<guidance>
```

An episode's `premise.md` names its format. Starting set:

| Format id | Runtime | Structure | Beats | Skips |
|---|---|---|---|---|
| `short-hook-payoff` | 15-20 s | hook and payoff | hook, payoff | outline |
| `short-four-act` | 30-45 s | kishotenketsu; needs no antagonist | introduction, development, twist, conclusion | outline |
| `short-circle` | 45-60 s | story circle folded into four quadrants | comfort and want, crossing and search, finding and price, return and change | outline |
| `serial-microdrama` | 60-90 s | one event per episode, open ending | pickup, event, value change, open question | outline |
| `episode-circle` | 3-11 min | story circle, eight steps | you, need, go, search, find, take, return, change | |
| `episode-twelve-beat` | 7-11 min | premise paragraph, then twelve beats split 3/6/3 | beat 1 to beat 12 | |
| `episode-acts` | 15-20 min | teaser, three or four acts, tag; A and B stories | teaser, act 1, act 2, act 3, tag | |

The runtimes and shot counts come from guidance for live-action and talking-head video. None is
tested on stylised animation: each is a starting value, and the lab changes it from results.

## 7. Shared stores

All are files in the production repository, changed only by pull request. The exceptions are the
append-only logs (ledger, results log, registries): the `factory` command that owns each one appends
to it on the base branch, and nothing else may write it.

| Store | Path | Written by | Notes |
|---|---|---|---|
| Series bible | `bible/charter.md`, `bible/canon/**`, `bible/amendments/A-<n>.json` | canon-keeper, by amendment | Each episode records the bible version it was built from |
| Playbook | `playbook/PB-<n>.md` | retro-agent, audience-analyst, render-reviewer | A lesson with evidence, confidence and an expiry date. Expired lessons are dropped |
| Pivot queue | issues labelled `pivot`, class `P0`..`P3` | researcher, trend-scout, audience-analyst | Each states what it touches and a hypothesis written before testing |
| Judge calibration set | `calibration/` | audience-analyst | Scenes labelled with real audience response. The only path by which audience data changes how judges score |
| Registries | `registry/assets.tsv`, `variants.tsv`, `posts.tsv` | orchestrators, on lock | Asset versions and dependents; file hash per variant; live post, hash and beat map |
| Platform rulebook | `rulebook/<platform>.json` | post-supervisor, from platform notes | Specs, safe zones, policy; read by the platform gate |
| Results log | `lab/results.tsv` | lab-director | Append-only: every experiment, kept or not |
| Ledger | `ledger/spend.tsv` | `factory gate`, `factory run` | Append-only: every gated job and its caller (the gate's row), and the real cost of its one call (the `run` row) |
| Media manifest | `media/manifest.tsv` | makers and operators | `id`, `sha256`, `bytes`, `source` per file. The bytes live in the media store, never in git |
| Rights file | `sources/rights.tsv` | the owner only | One row per work the production may draw on, with its holder and the basis. A row a role wants added is an issue labelled `owner` (section 16, "Rights") |
| Not-ours list | `research/not-ours.tsv` | researcher | The names of characters, places, creatures, works and marks that belong to someone else, each with its holder and the link where it was seen |
| Topics | `research/topics/<topic>.json` | researcher | The words, hashtags and named accounts to read on each platform. The reads start once the file is locked |
| Trend cards | `research/trends/<topic>.<platform>.tsv` | researcher, from the ranking command | Per video: link, id, rank at read time, read time and the role's note. No count |

Every factual line in research, bible and story artifacts carries one of `[text]`, `[art]`,
`[inferred]`, `[invented]`; the first two carry a source pointer.

What a platform says about other people's videos (counts, titles, comment text) is not a store in the
repository. It lives in the state directory with a read time and is refreshed or deleted within 30
days. It never enters git, because git history cannot forget (`docs/resources.md`, R8).

### Pivots

| Class | Example | Approval | Tested by | Limit |
|---|---|---|---|---|
| P0 texture | a real-world detail, a gag, a prop | showrunner | lint only | none |
| P1 beat | reworks a scene or a beat | showrunner, plus a blind win with the pitch judges | the scene loop | the episode's budget |
| P2 arc | adds or bends an arc | Braintrust supermajority (5 of 7), then an amendment | pivot lab: animatic variants, two episodes deep, blind against the plan | 3 a season |
| P3 canon break | contradicts canon or the charter | supermajority, a retcon plan, the devil's advocate signs off | pivot lab, plus replay against the whole canon ledger | 1 a season, with a cooldown |

A losing pivot returns to the queue with a cooldown and a playbook note. The lab only affects episodes
that have not shipped.

## 8. Markers

Only the first line of a comment counts. `<ROLE>` is the role id in upper case.

| First line | By | Body |
|---|---|---|
| `[<ORCHESTRATOR>] LAUNCH <role> <id>` | orchestrator | the exact prompt given, its sha256, model, harness version, base commit |
| `[<ORCHESTRATOR>] RULING` | orchestrator | what was decided, the charter or rule cited, how to overrule |
| `[<ORCHESTRATOR>] SELECTED #<pr>` | orchestrator | on a pitched ticket: the chosen pitch and the reason |
| `[<ORCHESTRATOR>] LOCK` | `factory lock` | the head and manifest hash locked, and the verdicts it relied on |
| `[<ROLE>] READY` / `ADDRESSED` / `BLOCKED` | maker, operator | head SHA, lint table, one evidence line per criterion; per note the change or a rebuttal; Missing / What was tried / Resume from |
| `[LINT] PASS` / `FAIL` / `ERROR` | `factory lint` | per check: result and reason; blockers `B1..Bn` |
| `[<ROLE>] APPROVED` / `NOTES` | checker | evidence per criterion; notes `B1..` (Blocker) and `I1..` (Issue) |
| `[<ROLE>] PREFERS A` / `PREFERS B` / `NO PREFERENCE` | judge | cited panels or frames; the blinding id |
| `[BRAINTRUST] VOTE <for>-<against>` | braintrust | on an amendment: the tally and each seat's reason |
| `[PRODUCER] GATE PASS` / `GATE STOP` | `factory gate` | the job or lock it covers, by hash; the cap and the ledger line |
| `[PLATFORM-GATE] PASS` / `FAIL` | `factory lint` | the variant's file hash |
| `[CHANNEL:<PLATFORM>] POSTED` / `VERIFIED` | channel operator | post id, file hash, visibility |
| `[ANALYST] SYMPTOMS` | analyst | symptom notes with beat IDs and evidence; never a fix |
| `[AUTORESEARCH] KEEP` / `NOISE` / `DISCARD` / `REBASELINE` | lab | metric, runs, noise floor, the results-log line |

Every verdict ends with `reviewed head <sha> manifest <hash>`, where `<sha>` is the full 40-character
commit id (the launcher writes it; an abbreviated one is read as no `reviewed head` line). The manifest hash is the sha256 of the
`media/manifest.tsv` rows the pull request adds or changes against its merge base, sorted, or `none`
when it adds none: it covers the media bytes the way the head covers the text.

A verdict counts only if all of these hold:

- it names the current head, or a head the current one differs from only by merges of the base branch;
- it names the current manifest hash;
- it is newer than the maker's last `READY` or `ADDRESSED`;
- it comes from the role the pipeline names for that stage.

For each role only its latest counting verdict is read. A checker's Blockers are open while its latest
counting verdict is the `NOTES` that lists them; that checker's later counting `APPROVED` closes them, and
another checker's does not.

`ERROR` means the check could not run and is never read as PASS or FAIL. After three rounds of notes
on one ticket the room's orchestrator rules.

No worker writes to GitHub. A worker ends with a result object, and the launcher posts it as that
role's marker, adding the head and manifest hash it read from git itself (`docs/harness.md`).

## 9. Lock

Merging a pull request is the lock. `factory lock` merges at most one pull request per run, and only when:

1. `[LINT] PASS` counts for the head, for every lint the stage names.
2. Every checker the stage names has a counting `APPROVED`, and no Blocker is open.
3. Every judge the stage names has a counting verdict on the head. Where the stage is `blind` and a locked version exists, the verdicts prefer the new one (for a panel, a majority).
4. The upstream stage of the same unit is locked, or the format skips it; in production, every cheaper stage of the shot is locked.
5. On a pitched ticket, the pull request is the one selected.
6. A pull request touching `bible/**` carries its amendment, the vote its class requires, and `canon-reviewer` `APPROVED`.
7. The changed files are inside the maker role's `writes` globs, and inside the room of the orchestrator that is locking.
8. A note raised at two screenings in a row has a change or a ruling that acts on it (rule 5).
9. `[PRODUCER] GATE PASS` covers the lock where section 11 requires one.
10. The head contains the current base, and there is no `hold` label.

A released episode's files are never changed by a later pull request (rule 6); the lock refuses it.

## 10. Lint

`factory lint` checks the working tree and returns PASS, FAIL or ERROR per check. Same input, same
result. The files it reads are the ones git would commit: tracked files and untracked files that no
ignore rule excludes. Outside a git work tree it reads every file except `.git/`. Checks are files in `kit/lib/lints/`, discovered by name, so adding a check edits no shared file.

Built first (text and data):

- **Provenance** (`provenance`). In files under `research/`, `bible/` and `episodes/`, every table row and list item
  carries one of `[text]`, `[art]`, `[inferred]`, `[invented]`. `[text]` and `[art]` carry a source
  pointer written as a link: `[text](sources/<file>)` or `[art](media:<id>)`. Facts in JSON and TSV
  ledgers carry the same tag and source as fields.
- **References** (`references`). Every relative link target in a Markdown file exists, and so does
  every `path`, `source` or `ref` value in a JSON file. Every `media:<id>` is in `media/manifest.tsv`.
- **Media** (`media`). No media bytes in git. Every `media/manifest.tsv` row has `id`, `sha256`, `bytes` and
  `source`, and ids are unique.
- **Structure** (`structure`). An episode's `premise.md` names a format that exists. `beats.md` has the format's
  beats as headings, in order, each with a duration, and the durations sum to a value inside the
  format's runtime. Every beat is covered by a scene in the outline and the script; every script line
  is in `lines.json`; every shot's duration, characters and set resolve; the edit list covers every beat ID.
- **Roles, formats and pipeline** (`kit`). Every role and format file parses, every maker's `checked_by`
  exists and is a checker or a judge, and every stage names roles and lints that exist. The kit checked
  is the one in the tree: `.factory/` in a production, `kit/` in the factory's own repository. A tree
  with neither passes. The check never falls back to the kit that is running it.
- **Markers** (`markers`). A comment's first line is a marker that role may post. Role kinds come from
  the kit in the tree; recorded comments with no kit to judge them by is ERROR.
- **Canon contradictions** (`canon`). The canon ledger is `bible/canon/ledger.tsv`: `subject`, `attribute`,
  `value`, `tag`, `source`, one row per permanent fact (a height, a lineage, who is dead). State that
  changes during a story is not canon; it lives in the continuity ledger. An episode asserts its facts
  in `episodes/<ep>/ledger.json` as `{subject, attribute, value, tag, scene}`. A contradiction is an
  episode fact with the same subject and attribute as a canon row and a different value, compared
  after trimming and lower-casing.
- **Spoiler and CTA** (`spoiler`). Titles, captions and CTAs against the intent packet's spoiler list and the
  rulebook's bait patterns.
- **Rights** (`rights`, built in #274). A canon sheet with `origin: source` cites a file or a media id
  that a row of `sources/rights.tsv` covers, and one with `origin: house` names an amendment that
  exists. Every rights row has a `holder` and a `basis` of `own`, `licence`, `permission` or
  `public-domain`. No name in `research/not-ours.tsv` appears in `episodes/**`, `bible/canon/**`,
  `bible/style/**`, `post/**` or `distribution/**`, as a whole word compared after trimming and
  lower-casing. Every `speaker` in `lines.json` has a canon character sheet, from the `script` stage
  on. `bible/charter.md`, `research/**` and `sources/**` are not checked against the list, so that they
  can name a comparable show or a trend. A generation request whose text contains a listed name is
  refused before any call. This check matches names; a likeness is judged by the checkers (section 4).

Built with their rooms (read from the engine or from media): the production and post checks in the
table in section 6. Each one is developed against a seeded defect it must fail on and a clean case it
must pass on.

## 11. Producer gate

`factory gate` is the only path to spending money or doing something that cannot be undone.

- **Caps** live in `production.env`: per episode and per season, for money, compute minutes,
  iterations per ticket and wall-clock time. The owner sets them once.
- **A gated job** (render, generation, voice, upload, public switch) is requested by an operator as a
  job file: inputs by hash, tool, expected cost. `factory gate` checks the caps, the ledger and the
  verdicts the job depends on, appends the ledger line and posts `GATE PASS` on the job's hash, or
  `GATE STOP` with the cap that tripped. `factory run` then makes the call with the credential, and
  refuses a job without a counting PASS. The credential does not enter the operator's session
  (`docs/harness.md`, section 8).
- **Locks that need a gate PASS**: a lock that hands a package to another room (the story package,
  the master, a platform package), and any lock on a ticket that has passed its iteration cap.
- **Release is staged**: private upload, a check that the platform finished processing and kept
  captions and disclosure, then a scheduled public switch that is its own gated job.
- **Kill switch**: `factory freeze` stops every launch and every gate in the production.
- **Monitors** the gate runs on every tick:

| Monitor | Trips when | Action |
|---|---|---|
| Gaming the judges | rubric scores rise while held-out judges and the retell test stay flat | freeze the run, rotate judges, open a retro |
| Charter drift | episode summaries move away from the charter across 3 episodes | force a charter review |
| Chasing metrics | engagement rises while distinctiveness falls | halve engagement's weight for one round |
| Canon contradiction | the lint finds a fact against the canon ledger | block the lock, route to head of story |
| No convergence | no blind win after K rounds at a level | one re-break to the outline, then ship the best so far |
| Budget | cost or time passes its cap | stop the run, ship the best so far |

## 12. Loops

Each room runs three nested loops. A package leaves a room when the loop that locks it exits; only
the outermost loop changes the room's own rules.

| Room | Inner loop, exit | Middle loop, exit | Outer loop, exit |
|---|---|---|---|
| story | scene (hours): blind win and lint PASS | episode (days): showrunner lock | season (weeks): retro passes its replay tests. Only this loop amends the bible |
| production | shot (hours): fresh PASS and APPROVED on the head | sequence (days): cross-shot diff clean | episode (week): master matches the locked cut |
| post | variant (minutes): PASS on the file hash, judge approval | package (hours): every variant locked | release window (days): producer gate PASS |
| distribution | post (hours): VERIFIED on the live post | window (7 days): snapshot frozen | season: the story room answers the platform notes |
| lab | experiment (minutes): beats the best by twice the noise, twice | program (days): 5 NOISE in a row, or its cap | metric (season): the new metric predicts outcomes better |

Orchestrators do not poll. A timer runs `factory tick`, which dispatches the next mechanical step
(launch the named checker, relaunch a maker on notes, run the lint, run the gate, lock at most one
ticket per room) from GitHub state alone. For a ruling after three rounds and for a pitch selection
it launches the room's orchestrator role itself, once per pull request and once per ticket; from the
third round on, every round of notes needs its own ruling. It wakes an orchestrator (a `NEED` line and
the label `needs:orchestrator`) only when that one run did not settle the judgment, and for a pivot
triage or a BLOCKED worker.

## 13. The lab

Each loop changes one thing, measures one number, keeps the change only if it beats the best result
by more than the noise, and logs every run. Frozen for the experimenter: the metric, the harness of
the experiment, the tests, the judges and the keep threshold.

| Loop | One metric | Lever | Noise |
|---|---|---|---|
| Render cost | GPU minutes per finished second, holding a quality floor | render graph settings, LODs, lighting settings | low |
| Checker strength | share of seeded defects caught in the frozen defect corpus | lint rules and thresholds | none |
| Judge calibration | rank agreement between judge scores and real retention on held-out episodes | judge rubrics and prompts | medium |
| Craft | blind win rate of new role instructions, replayed on past episodes, scored by held-out judges | writer, artist, animator and CTA role files | medium |
| Packaging (live) | average watch time or early hold rate per platform | hooks, caption style, CTA placement, thumbnails | high |

Verdicts: `KEEP` beats the best result by more than the threshold, twice. `NOISE` is inside the
threshold either way. `DISCARD` is worse than the best by more than the threshold, or breaks a frozen
floor. A KEEP becomes a pull request that the `experimenter` opens in the target room and that room's
orchestrator locks.

Before a loop starts it runs 5 to 10 no-op controls and sets its keep threshold at twice their
spread or more. A KEEP is re-run once before it counts. Every 10 to 15 runs a no-op control checks for
drift; drift past the threshold logs `REBASELINE`. A loop never changes its own metric: that is a
metric amendment, accepted only if the new metric predicts real audience outcomes better on held-out
episodes. Live platform loops only confirm what offline loops already found.

**Role evals.** Every role has an eval suite in `kit/evals/<role>/`: seeded cases with an outcome a
program can grade. A checker's eval is its catch rate on seeded defects and its false-alarm rate on
clean cases. A maker's eval is its lint pass rate and its blind win rate on replayed tickets. The lab
owns the suites; no role and no engineering team may edit the suite it is measured by. These evals are
what "the harness is failing" means in `docs/harness.md`.

## 14. Runtime

Specified in `docs/harness.md` and `docs/resources.md`. The rules:

- **One harness.** Every role runs on the same agent harness, used only through its public surface.
  It is forked for one process only when that process's role evals keep failing for a cause in the
  harness, and then a separate engineering team owns the fork.
- **Inference by login.** Models are reached through subscription (OAuth) logins. No role is given an
  inference API key.
- **Tools by key.** Everything that is not a model (generators, voice, render nodes, platforms,
  storage) is reached with a credential that works unattended. A role gets only the credentials its
  role file names, and long-lived credentials do not reach a worker when a broker can hold them.
- **Knowledge in Fermi, state in GitHub.** Roles read procedures, craft knowledge and recall from the
  Fermi MCP server. Nothing a gate reads lives only there: a lesson binds once it is a playbook file
  locked by pull request.
- **Other people's videos are read by a program.** `factory tick` asks `factory run` for the reads of
  the locked topics, through the platforms' official APIs only and inside a daily budget set in
  `production.env`. No role holds a platform key, and no video file, frame, audio or thumbnail is
  fetched (`docs/resources.md`, R7 to R9).
- **Only programs write state.** No agent holds the GitHub credential. `factory` commits a worker's
  files, opens the pull request and posts the markers, so a role cannot post another role's verdict.

## 15. Command line

`kit/factory <command>` runs `kit/lib/cmd_<command>.py`. The dispatcher discovers commands by file
name, so adding a command edits no shared file. Python 3 standard library, bash, `git`, `gh`.

| Command | Does |
|---|---|
| `factory roles [--check]` | list roles by room; `--check` validates every role file, format and the pipeline |
| `factory doctor` | check every resource in `kit/resources.json` the installed rooms need; prints names and states, never values |
| `factory launch <role> <ticket-or-pr> [task-file]` | make a worktree, render the prompt, post `LAUNCH`, start one detached worker with that role's tools, knowledge and secrets; when it exits, commit its files inside the role's `writes` globs and post its result as the role's marker |
| `factory status [--room R]` | every open ticket and pull request: room, unit, stage, state, next step |
| `factory lint [paths]` | section 10; `--post <pr>` posts the `[LINT]` marker |
| `factory blind <pr>` | prepare a blinded old-against-new pair for a judge and record the key outside the worktree |
| `factory gate <job-or-pr>` | section 11 |
| `factory run <job>` | make one gated call with its credential; write the outputs, the manifest rows and the ledger line |
| `factory media put <file>` / `link` / `check` | the media store client: store a file's bytes and add its manifest row, print the public link of a row, check that the links serve the manifest's bytes |
| `factory assets put <file> [--apply]` / `get <sha256> <path>` / `check <sha256> ...` | the asset store client: store or fetch an engine binary under its sha256. Every request is signed and no object or file is overwritten |
| `factory lock [--room R] [--apply]` | section 9; without `--apply` it only reports |
| `factory tick [--apply]` | section 12: dispatch the next mechanical steps from GitHub state |
| `factory freeze` / `unfreeze` | the kill switch |
| `factory init <dir> [--rooms ...]` | install the kit into a production repository as `.factory/` |

## 16. A production repository

One repository per show. `factory init` creates:

```
.factory/                 the installed kit
production.env            repository, base branch, rooms installed, caps, media store, worker command (no secrets)
rules/production-rules.md pasted into every role prompt after the rules of power
sources/                  raw inputs as text; rights.tsv, the works the production may draw on
research/                 researcher output; topics/ and trends/ (other people's videos);
                          not-ours.tsv, the names that belong to someone else
bible/                    charter.md, canon/, amendments/, style/
playbook/  calibration/  registry/  rulebook/  ledger/  lab/
episodes/<ep>/            premise.md, beats.md, outline.md, script.md, lines.json, ledger.json,
                          sequences/<seq>/shots/, reel/edit-list.json, intent-packet.json
production/<ep>/          shot tickets' scene scripts and manifests (engine binaries live in the asset store)
post/<ep>/  distribution/<ep>/
media/manifest.tsv
```

### Episode files

These are the shapes the `structure` lint checks. IDs are stable once locked: beats `B<n>`, scenes
`S<n>`, lines `L<n>`, sequences `Q<n>`, shots `<sequence>-<n>`.

| File | Shape |
|---|---|
| `premise.md` | front matter `format: <format id>` and `logline: <one sentence>`; then the pitch |
| `beats.md` | one `## B<n> <beat name>` heading per beat of the format, in the format's order; the first line under each is `duration: <seconds>`. The durations sum to a value inside the format's runtime |
| `outline.md` | one `## S<n> <title>` heading per scene; the first line under each is `beats: B<n>, ...`. Every beat is named by at least one scene. Absent when the format skips `outline` |
| `script.md` | the same `## S<n>` headings as the outline (or, without an outline, each followed by a `beats:` line); a spoken line starts `L<n> <SPEAKER>:` |
| `lines.json` | a list of `{id, scene, speaker, text}`: exactly the `L<n>` lines of the script |
| `ledger.json` | a list of `{subject, attribute, value, tag, source, scene}`: the facts the episode asserts |
| `sequences/<Q>/sequence.json` | `{id, scenes: [S<n>, ...]}`. Every scene is in exactly one sequence |
| `sequences/<Q>/shots/<shot>.json` | `{id, scene, beat, duration, characters: [...], set, camera, action}`, optional `panel: media:<id>`. `duration` is seconds, above zero. Each character is a file `bible/canon/characters/<id>.md` and the set a file `bible/canon/places/<id>.md` |
| `reel/edit-list.json` | `{fps, events: [{shot, beat, start, duration}]}`: events in order with no gap or overlap, every shot exists, every beat ID appears, and the total is inside the format's runtime |
| `intent-packet.json` | `{hook, spoilers: [...], cta: [...], tone}` |

The lint checks a file only when it exists, and the files a stage needs only from that stage on:
a premise alone passes; a script without `lines.json` fails.

Order of authority when two files disagree: the rules of power, the charter, the production rules,
the canon ledger, the episode ledger, the role file, the task.

### Bible sheets

The bible is the show's memory of its characters and places. Every stage reads it, so a character or
a place is described once and reused by every later episode. Each subject has two sheets (the lint for
them is #235):

| File | Written by | Shape |
|---|---|---|
| `bible/canon/characters/<id>.md` | canon-keeper, by amendment | who the character is, then `## Behaviour` (how it acts and speaks) and `## Motivation` (what it wants and why), neither empty |
| `bible/canon/places/<id>.md` | canon-keeper, by amendment | what the place is and what happens there |
| `bible/style/characters/<id>.md`, `bible/style/places/<id>.md` | art-director, in the `style` stage | the model sheet: how the subject looks, with at least one reference image as `media:<id>` |

Where a production already holds art of a subject, the model sheet cites that art and nothing is
designed. A subject with no art goes through design before a board may use it: an amendment adds its
canon sheet, the art director designs its model sheet from that sheet and the style lock, and the look
reviewer checks it. An approved design is then the reference, as source art is. A generated panel
starts from the reference images of the characters and the set in its shot. Production assets are
built to match them.

### Other people's videos

The production watches what is trending for the topics it might tell stories about, one platform at
a time, because a trend on one platform is not a trend on another.

| File | Written by | Shape |
|---|---|---|
| `research/topics/<topic>.json` | researcher | `{topic, youtube: {words: [...], region, days}, tiktok: {hashtags: [...]}, instagram: {accounts: [...], hashtags: [...]}}`. A platform that is not watched for the topic is left out |
| `research/trends/<topic>.<platform>.tsv` | researcher, from the ranking command | one row per video and ranking: `ranking`, `rank`, `id`, `link`, `read_at`, `note`. `note` is the role's own words. No count, title or comment text |

- **Topics.** A topic file names what to read. The reads start once it is locked, and stop when it
  is removed.
- **Reads.** A program makes them, never a role: `factory tick` asks `factory run` for the reads of
  the locked topics, inside a daily budget set in `production.env` (YouTube allows 100 searches a
  day). A read costs no money and changes nothing, so it is not a gated job. No role holds a key.
- **Expiry.** Counts, titles and comment text go to the state directory with a read time, and are
  refreshed or deleted within 30 days. They never enter git.
- **What git keeps.** The trend card: the link and id of each video, its rank at read time, the read
  time, and the notes a role wrote in its own words.
- **Who reads.** `researcher` and `trend-scout` get a snapshot of the current reads and rankings of
  their ticket's topic in their working directory at launch, outside git, removed when the run ends.
- **One ranking per platform.** Rankings are computed with no model and are never merged across
  platforms, because a view is not the same thing on each. A trend reaches the show only as a pivot
  proposal (section 7).

### Rights

A production uses only characters, places, creatures, names and marks that are its own: they come
from a work listed in its rights file, or they were designed in the production and cleared. No third
party's character, name or likeness appears in a story, an image, a prompt or a post. The `rights`
lint (#274) adds this to the rules of power as rule 8.

| File | Written by | Shape |
|---|---|---|
| `sources/rights.tsv` | the owner only; `factory init` creates it with the header | one row per work the production may draw on: `path` (the files under `sources/` or the media sources the row covers), `work`, `holder`, `basis` (`own`, `licence`, `permission` or `public-domain`), `note` |
| `research/not-ours.tsv` | researcher | one row per name that belongs to someone else: `name`, `holder`, `link` (where the name was seen) |
| every sheet under `bible/canon/characters/` and `bible/canon/places/` | canon-keeper, by amendment | one line `origin: source` with the file or the media id the subject comes from, or `origin: house` with the amendment that added it |

- **Origin.** A subject is `source` when it comes from a work in the rights file, and `house` when it
  was designed in the production. The amendment that adds a `house` subject is what clears it. A
  subject with source art is `source` and is not designed again.
- **The rights file is the owner's.** No role's `writes` covers `sources/**`. The kit ships no rights
  file, and a row a role wants added is an issue labelled `owner`.
- **A trend can be used, its character cannot.** What a trend offers is a format, a subject or a
  rhythm. The character, the sound and the shot stay with their owner, and `researcher` lists each
  such name in `research/not-ours.tsv`.
- **Where the list is checked.** Where work reaches the audience or a generator: `episodes/**`,
  `bible/canon/**`, `bible/style/**`, `post/**`, `distribution/**` and the text of a generation
  request. Not in `bible/charter.md`, `research/**` or `sources/**`, which must be able to name a
  comparable show or a trend.
- **A name is linted, a likeness is judged.** A program can match a name. It cannot tell that a
  drawing looks like someone else's character, so `canon-reviewer`, `look-reviewer` and
  `packaging-judge` raise a Blocker (section 4). A check on rendered frames waits for the identity
  metrics of wave 4 (#131).
- No role and no stage is added for rights.

## 17. Build order

The kit is built in this repository through the orchestration pattern in `CLAUDE.md`. Checks come first.

| Wave | Scope | Done when |
|---|---|---|
| 1 | Machinery for text stages: role and format files and their parser, the story room's pipeline entries, markers and verdict freshness, lint framework and the text lints, resources register and `factory doctor` | every lint and `factory roles --check` fail on each seeded defect and pass on a clean fixture |
| 2 | `factory launch` on the harness, `status`, `blind`, `gate` and the ledger, `lock`, `tick`, `init`; story-room role files and their evals | `factory roles --check` passes on the shipped kit; a fixture ticket goes from LAUNCH to LOCK offline; role evals have a measured noise floor |
| 3 | The sample production runs the story room: research to a locked story reel | one episode's story package is locked with no human approval |
| 4 | Production verification tooling: engine lints, golden test levels, seeded-defect corpus, render reproducibility harness. Platform approvals started | every production lint fails on its seeded defect |
| 5 | Pilot: one 10-second shot through every production stage to private uploads | the shot is locked at every stage and visible privately on each platform |
| 6 | One full episode on YouTube; then vertical cuts, captions, CTAs, TikTok and Instagram; offline lab loops | the retention curve maps onto beat IDs; each loop has a noise floor |
| 7 | Live loops: judge calibration on real retention, packaging experiments | judge rankings agree with real retention above a set threshold |
