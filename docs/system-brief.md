# Content factory: system brief (v0)

Showrunner's specification, 2026-11-11. This is what the repository builds. `docs/architecture.md`
is the picture of it: data flows, responsibilities, tools and the GitHub model. The research behind it
is in `docs/research/` (start with `01-production-practice.md`). This brief wins where they disagree.

## 1. What it is

A system that runs story production (research, canon, story, script, storyboard, and later 3D
build, render, edit and QC) the way a studio does, with agents in the roles and GitHub as the only
state. It is the content counterpart of the software pattern this repo is itself built with
(`.agents/orchestrator/`; the two are compared in `docs/architecture.md`). It is project-agnostic.
The first sample production is the Primal TCG world with Argelia as lead (`samples/primal/README.md`).

It differs from the software pattern in six ways, and these are the reason it is a new system:

1. **Roles are data.** A role is one file in `kit/roles/`. Adding a department adds files, not code.
2. **A stage ladder with locks.** Work on a unit moves through ordered stages. A stage must be locked
   before the next stage of that unit may start. Cheap stages come first, so failure is cheap.
3. **Rooms.** A stage can run as a room: several makers pitch the same ticket in parallel, a checker
   scores every pitch on one rubric, and the showrunner selects one.
4. **Notes with severity.** Checkers give notes marked `Blocker` or `Issue`. Only a Blocker stops a lock.
5. **Canon is protected.** Canon changes only through a canon pull request, and a canon change marks
   downstream locked work that pinned the old canon as stale.
6. **Provenance on every fact.** Each factual line in research, canon and story artifacts is tagged
   `[text]`, `[art]`, `[inferred]` or `[invented]`, with a source pointer for the first two.

## 2. People and authority

| Who | Studio analog | Authority |
|---|---|---|
| Owner | studio / network | Greenlights, money, publishing, canon rulings they choose to make |
| Showrunner | showrunner, line producer | The orchestrator session. Files tickets, launches roles, rules on disputes, selects in rooms, records owner greenlights, runs every paid or irreversible job, locks. The only one who merges |
| Makers | writers, artists, researchers | Produce one artifact per ticket on their own branch. Never merge |
| Checkers | script coordinator, script supervisor, QC | Give notes on the exact head commit. Never push to the maker's branch, never merge |

Checkers never author. This is a design choice carried from the software pattern; studios do not work
this way (an animation director corrects by redrawing). Correction is a maker relaunched on the same
pull request to address the notes.

## 3. Departments and roles

v0 builds the Development and Story departments. The others are listed so the formats leave room for them.

| Role id | Department | Kind | Studio analog | Artifact | Checked by |
|---|---|---|---|---|---|
| `researcher` | development | maker | researcher, writers' assistant | `research/<topic>.md` plus an evidence table | `fact-checker` |
| `fact-checker` | development | checker | research checker | notes: sampled claims re-derived from the sources | |
| `canon-keeper` | development | maker | script coordinator (compiles the bible) | `canon/**`: bible, character and place sheets, rulings ledger | `canon-reviewer` |
| `canon-reviewer` | development | checker | chief animation director | notes, plus the list of locked artifacts the change makes stale | |
| `story-writer` | story | maker | staff writer | premise pitch, then `episodes/<ep>/beats.md` | `story-editor` |
| `script-writer` | story | maker | episode writer | `episodes/<ep>/outline.md`, then `script.md`, `lines.json` and `ledger.json` | `story-editor` |
| `story-editor` | story | checker | story editor | notes: the format's structure lint results plus a story rubric | |
| `storyboard-artist` | story | maker | director, board artist, DP | `episodes/<ep>/shots/<id>.yaml` and the board | `continuity-supervisor` |
| `continuity-supervisor` | story | checker | script supervisor | notes: wardrobe, props, facts, axis and eyeline against canon and the episode ledger | |

Planned, not in v0: animatic (timed panels with scratch voice, the last cheap gate before render
spend; the board's owner greenlight moves to it), production designer, character designer, asset builder, layout, animator,
lighting and render (3D, driven by scripts so a scene can be rebuilt and measured), editor, sound,
technical QC.

## 4. Role file format

`kit/roles/<role-id>.md`: front matter of plain `key: value` lines between `---` lines, then the
prompt body. No YAML library; values are strings, and `writes` and `reads` are comma-separated globs.

```
---
role: researcher
department: development
kind: maker                # maker | checker
analog: studio researcher
stage: research            # the pipeline stage this role works in (a role may list several, comma-separated)
writes: research/**        # globs a maker may change; empty for a checker
reads: sources/**, canon/**
checked_by: fact-checker   # makers only
model: builder             # builder | verifier (which configured model runs it)
---
<prompt body>
```

The prompt body uses `{{NAME}}` placeholders. Required in every body: `{{REPO}}`, `{{TICKET}}`,
`{{WORKTREE}}`, `{{TASK}}`, `{{RULES}}` (the production's rules file). Makers also get `{{BRANCH}}`;
checkers also get `{{PR}}`. Every body states the read-first list, the hard rules, the exact markers the
role may post, the provenance rule, and the BLOCKED protocol. Checker bodies contain their rubric.

## 5. Pipeline

`kit/pipeline.json` is the default ladder; a production may override it. One entry per stage:

```json
{"stage": "beats", "unit": "episode", "after": "premise", "maker": "story-writer",
 "checker": "story-editor", "paths": ["episodes/*/beats.md"], "room": false, "greenlight": false}
```

v0 ladder:

| Stage | Unit | After | Maker | Checker | Room | Owner greenlight |
|---|---|---|---|---|---|---|
| `research` | topic | | researcher | fact-checker | no | no |
| `canon` | canon change | research (when it cites new research) | canon-keeper | canon-reviewer | no | no |
| `premise` | episode | | story-writer | story-editor | yes, 3 pitches | yes |
| `beats` | episode | premise | story-writer | story-editor | no | no |
| `outline` | episode | beats | script-writer | story-editor | no | no |
| `script` | episode | outline | script-writer | story-editor | no | no |
| `board` | episode | script | storyboard-artist | continuity-supervisor | no | yes |

A ticket is a GitHub issue titled `[KEY] summary` with labels `tracked`, `stage:<stage>` and
`unit:<unit-id>` (for example `unit:ep01`). One ticket, one maker, one branch, one pull request,
except in a room, where each pitch has its own pull request for the same ticket.

The board ticket carries the showrunner's tone notes: what each scene is for and how it should feel
(the studio tone meeting). The storyboard artist treats them as acceptance criteria.

### Formats

A format is the story formula for a runtime. It is one file in `kit/formats/`, with the same front
matter rules as a role file, then guidance for the writer and the story editor:

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
| `episode-circle` | 3-11 min | Dan Harmon's story circle | you, need, go, search, find, take, return, change | |
| `episode-twelve-beat` | 7-11 min | premise paragraph, then twelve beats split 3/6/3 | beat 1 to beat 12 | |
| `episode-acts` | 15-20 min | teaser, three or four acts, tag; A and B stories | teaser, act 1, act 2, act 3, tag | |

The runtimes and shot counts come from guidance written for live-action and talking-head video. None
is tested on stylised animation, so treat each as a starting value and change it from results.

## 6. Markers

Only the first line of a comment counts. `<ROLE>` is the role id in upper case.

| First line | By | Body |
|---|---|---|
| `[<ROLE>] READY` | maker | head SHA, lint table, one evidence line per acceptance criterion |
| `[<ROLE>] NOTES` | checker | `Reviewed head <sha>`, notes N1..Nn: `Blocker` or `Issue`, the criterion, evidence, how to reproduce |
| `[<ROLE>] ADDRESSED` | maker | per note: the change and its commit, or a reasoned rebuttal |
| `[<ROLE>] APPROVED` | checker | `Reviewed head <sha>`, evidence per criterion, any remaining `Issue` notes |
| `[<ROLE>] BLOCKED` | maker or checker | Missing / What was tried / Resume from |
| `[SHOWRUNNER] LAUNCH <role> <id>` | showrunner | the exact prompt given, its sha256, model, base commit |
| `[SHOWRUNNER] RULING` | showrunner | what was decided, why, how to overrule |
| `[SHOWRUNNER] SELECTED #<pr>` | showrunner | on a room ticket: the chosen pitch and the reason |
| `[SHOWRUNNER] GREENLIGHT` | showrunner | the owner's words quoted, with the date |

A checker verdict counts only if it is newer than the maker's last `READY` or `ADDRESSED`, its
`Reviewed head` is the pull request's current head, and it comes from the role the pipeline names as
that stage's checker. After three rounds of notes the showrunner rules.

## 7. Lock

Merging a pull request is the lock. `factory lock` merges at most one pull request per run, and only when:

1. The stage's checker has a counting `APPROVED`, and no `Blocker` note is open.
2. The upstream stage for the same unit is locked, or the unit's format skips it.
3. The stage's owner greenlight is recorded, where the pipeline requires one.
4. In a room, the pull request is the one the showrunner selected.
5. A pull request touching `canon/**` has `APPROVED` from `canon-reviewer`, whatever its stage.
6. The changed files are inside the maker role's `writes` globs.
7. `factory lint` is green on the head, the head contains the current base, and there is no `hold` label.

## 8. Lints (deterministic)

`factory lint` checks the working tree and returns PASS, FAIL or ERROR per check. ERROR means the check
could not run, and is never treated as FAIL.

- **Provenance.** In files under `research/`, `canon/` and `episodes/`, every table row and list item
  that states a fact carries one of `[text]`, `[art]`, `[inferred]`, `[invented]`. `[text]` and `[art]`
  carry a source pointer (a path under `sources/` or a media id).
- **References.** Every repository path a file cites exists. Every media id cited is in `media/manifest.tsv`.
- **Media.** Media files are never committed. `media/manifest.tsv` holds `id`, `sha256`, `bytes`,
  `source` per file; the bytes live outside git in the media store the production's config names.
- **Structure.** An episode's `premise.md` names a format that exists. `beats.md` has the format's beats
  as headings, in order, each with a duration, and the durations sum to a value inside the format's runtime.
- **Roles, formats and pipeline.** Every role and format file parses, every maker's `checked_by` exists
  and is a checker, and every stage in the pipeline names roles that exist.

## 9. Command line

`kit/factory <command>` runs `kit/lib/cmd_<command>.py`. The dispatcher discovers commands by file
name, so adding a command never edits a shared file. Python 3 standard library, bash, `git`, `gh`.

| Command | Does |
|---|---|
| `factory roles [--check]` | list roles by department; `--check` validates every role file and the pipeline |
| `factory launch <role> <ticket-or-pr> [task-file]` | make a worktree, render the role prompt, post `[SHOWRUNNER] LAUNCH`, start one detached worker |
| `factory status` | every open ticket and pull request: unit, stage, state, next step |
| `factory lint [paths]` | the checks in section 8 |
| `factory lock [--apply]` | the gate in section 7; without `--apply` it only reports |
| `factory init <dir>` | install the kit into a production repository as `.factory/` |

## 10. A production repository

One repository per series. `factory init` creates:

```
.factory/                 the installed kit
production.env            repository, base branch, media store path, worker command (no secrets)
rules/production-rules.md pasted into every role prompt
sources/                  raw inputs as text
research/                 researcher output
canon/                    bible, characters/, places/, rulings.md
episodes/<ep>/            premise.md, beats.md, outline.md, script.md, lines.json, ledger.json, shots/
media/manifest.tsv
```

Order of authority when two files disagree: owner ruling, production rules, canon, episode ledger,
role prompt, task.

## 11. Build order

- **Wave 1 (parallel, disjoint files):** role registry and launcher; the nine role files and the seven
  format files; pipeline, status and lock; lints.
- **Wave 2:** `factory init`, then the Primal sample imported into its own repository with it.
- **Wave 3:** the sample's first tickets run through the system: Three Aces research, the owner's
  rulings into canon, an episode premise room.
- **Later:** the animatic stage, 3D departments (`docs/research/04-3d-pipeline.md`, `05-ue5.md`), post
  and technical QC, signed job records, a board, a heartbeat.
