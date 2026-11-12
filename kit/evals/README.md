# Role evals

Each directory `evals/<role>/` is the eval suite of one role (brief section 13): cases with an outcome a
program can grade. `factory eval <role>` runs a suite; `lib/evals.py` loads and grades it.

## Who owns the suites

The lab owns the suites. No role and no engineering team may edit the suite it is measured by: a suite
written by the author of the prompt it measures measures nothing. No role's `writes` reaches `evals/`.

## Case format

A case is the directory `evals/<role>/<case-id>/` holding `case.json`, `task.md` and `tree/`, the
case's own files (a `pair` case holds `old/` and `new/` in place of `tree/`).

`factory eval` builds the production around them: it writes what `factory init` writes, with the kit
less `evals/`, and copies `tree/` over it, so a tree file wins and no answer is in the run directory.
A pair case's run directory holds only `A/` and `B/`, each a version plus the init charter and canon
ledger where the version has none, the same bytes on both sides.

| Role kind | `case.json` | Passes when |
|---|---|---|
| checker | `{"kind": "seeded", "catches": [regex, ...]}` | NOTES, every regex in a Blocker |
| checker | `{"kind": "clean"}` | no NOTES with a Blocker, and not BLOCKED |
| maker | `{"kind": "replay", "lint": [...], "expect_files": [glob, ...]}` | READY, files present, lints pass |
| judge | `{"kind": "pair", "better": "old"}` or `"new"` | PREFERS the side that shows `better` |

A seeded case plants exactly one defect that is the role's job and that no lint of its stages catches;
its tree passes those lints. A clean case is a near miss: it looks suspicious and is correct. A
`catches` regex matches a stable token of the defect (a scene, line, shot or palette id, a character
id, a value), never prose. A replay case's `lint` is its stage's `lint` in `pipeline.json`. A pair
case's two versions differ for one reason the rubric names and in no other way.

## Case ids

A case id is `<nn>-<slug>`: two digits, then lower-case letters, digits and hyphens. The id is in the
prompt the role reads, so the slug names the subject of the tree (`07-harbour-night`), never the kind of
case, the defect or the better side. In a checker's suite seeded and clean cases are numbered in mixed
order. A `task.md` never names the defect or says what kind of case it is; a pair case's `task.md`
calls the two versions `A/` and `B/`.

## Roles with no suite yet

| Role | Why not yet | Wave |
|---|---|---|
| `showrunner` | an orchestrator: its outcome is a locked package over many tickets, which needs the story room running end to end | 3 |
| `devils-advocate` | its notes argue against a direction across every stage; a gradable case needs real tickets to replay | 3 |
| `researcher` | its output is sourced from material it gathers; a replay needs recorded sources from a real run | 3 |
| `canon-keeper` | an amendment is replayed from real research and canon; the first exist when the sample production runs | 3 |
| `art-director` | its output is model sheets and media in the media store, which no offline replay produces | 4 |
| `render-reviewer` | it reviews renders; its seeded cases come from the seeded-defect corpus | 4 |
| `retro-agent` | its proposals are measured by the lab loops they start, which run offline from wave 6 | 6 |
| `trend-scout` | an analyst: its outcome is measured against platform data, first available in wave 6 | 6 |
| `audience-analyst` | an analyst: its outcome is measured against platform data, first available in wave 6 | 6 |
| `braintrust` | a judge calibrated against real retention (judge calibration), first available in wave 6 | 6 |
| `audience-sim` | a judge calibrated against real retention (judge calibration), first available in wave 6 | 6 |
| `distinctiveness-judge` | a judge calibrated against real retention (judge calibration), first available in wave 6 | 6 |
| `held-out-judge` | its cases are held out: it never has a suite in this kit, in any wave | never |
