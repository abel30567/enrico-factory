# The seeded-defect corpus

The frozen defect corpus of the checks (brief section 13, "Checker strength"): for each check, the
seeded defects it must fail on and the clean cases it must pass on. `factory corpus` runs it.

## Layout

A case is the directory `corpus/<check>/<case-id>/` holding `case.json` and `tree/`. `<check>` is the
id of a check in `lib/lints/`. `<case-id>` is `<nn>-<slug>`: two digits, a hyphen, then lower-case
letters and digits in hyphen-separated words. Files directly under `corpus/` or `corpus/<check>/`
(such as this README) and directories whose name starts with `.` are ignored.

## `case.json`

Exactly `{"kind": "clean"}`, or `{"kind": "seeded", "catches": [regex, ...]}` with at least one
non-empty regular expression, and no other key. No model reads it. A seeded case is caught when the
check fails and every regex matches a finding written as `<path>:<line> <rule> <message>`. A clean
case passes when the check passes. A check that errors neither catches nor passes.

## The command

`factory corpus [--corpus DIR] [--pipeline FILE]` copies each case's `tree/` to a new temporary
directory, runs the case's check there and prints, per check, the seeded cases caught and the clean
cases passed, then the totals and the share caught. It measures every check with a directory here
and every check a production stage of `pipeline.json` names; each needs at least one seeded case and
one clean case. Exit 0 when every check passes, 1 when a check misses a defect, raises a false
alarm or lacks a case, 2 on a malformed corpus.

## Who writes the cases

No role and no engineering team may edit the suite it is measured by: a check's cases are not written in the pull request that writes or changes the check.

A case is made from a golden level in `evals/golden/<level>/`: its tree holds the level's recorded files, and a seeded case changes one value.
