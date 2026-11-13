# Role evals

Each directory `evals/<role>/` is the eval suite of one role (brief section 13): cases with an outcome a
program can grade. `factory eval <role>` runs a suite; `lib/evals.py` loads and grades it.

## Who owns the suites

The lab owns the suites. No role and no engineering team may edit the suite it is measured by: a suite
written by the author of the prompt it measures measures nothing. No role's `writes` reaches `evals/`.

## Case format

A case is the directory `evals/<role>/<case-id>/` holding `case.json`, `task.md` and `tree/`, the
case's own files (a `pair` case holds `old/` and `new/` in place of `tree/`). Beside its cases a suite
holds `suite.json`, `{"cases": N}`: the number of case directories it holds.

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

Every file `roles/<role>.md` has exactly one of a suite `evals/<role>/` or a note `evals/<role>.no-suite.md`.
The note is four lines: `` # `<role>`: no suite yet ``, an empty line, `Why not yet: <reason>` and
`Wave: <n>`, the wave that adds the suite (`never` for `held-out-judge`: its cases are held out, and it never
has a suite in this kit, in any wave). A ticket that adds a suite deletes the role's note.
