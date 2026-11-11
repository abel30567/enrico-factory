# Autonomous Story Studio: adopt the design, find the resources, start the build

Owner request, 2026-11-11: the attached "Autonomous Story Studio" proposal is how the factory runs.
It gives the architecture and roles but not the resources and tools. Update the repository to it, find
what a run needs, and build the system with the orchestrated development pattern. hbar is the harness
and is treated as a primitive: fork it, with a dedicated engineering team, only if evals keep failing.
Inference by OAuth login, tooling by API key, Fermi MCP as the agents' knowledge layer.

Success: a new session can read the repository and know (a) how the studio runs, (b) every resource a
run needs and how an unattended agent reaches it, (c) which issue builds which part; and the first
build wave is in flight through `orch`.

- [x] Keep the proposal in the tree: `docs/design/autonomous-story-studio.html` (checked: no private strings).
- [x] `docs/system-brief.md` v1: rules of power, five rooms with their own orchestrators, the producer
      gate in place of owner greenlights, role kinds, stores, markers, lock, loops, pivots, the lab.
- [x] `docs/harness.md`: hbar as a primitive, how a role is launched, the fork rule.
- [x] `docs/resources.md` and `kit/resources.json`: the register of everything a run needs
      (inference logins, tool keys, Fermi, local tools, engine and render nodes, platform approvals).
- [x] `docs/architecture.md` redrawn to v1.
- [x] `CLAUDE.md`, `README.md`, project rules updated.
- [x] Wave 1 issues #1 to #6 filed with `orch task`; builders for #1, #2 and #3 launched on disjoint files
      (#4 to #6 depend on them). The `factory doctor` ticket follows the register.
- [x] A wake set so reviews and merges continue without the owner.

## Wave 1 progress

State lives on GitHub (`orch status`); this list is the record of what merged.

- [x] #1 dispatcher, front matter, roles, formats, story pipeline (PR #9, merged 2026-11-11 after one fix round)
- [x] #2 markers and verdict freshness (PR #7, merged 2026-11-11 after one fix round)
- [x] #3 lint framework, provenance, references, media (PR #8, merged 2026-11-11)
- [x] #4 structure lint (PR #18, merged 2026-11-11)
- [x] #5 kit and markers lints (PR #17, merged 2026-11-11 after a BLOCKED ruling)
- [x] #6 canon and spoiler lints (PR #14, merged 2026-11-11 after a BLOCKED ruling)
- [x] #10 `factory doctor` (PR #12, merged 2026-11-11)
- [x] #11 follow-up: lint reads the files git would commit (PR #16, merged 2026-11-11)
- [x] #13 follow-up: doctor, a command that cannot start is a failed check (PR #15, merged 2026-11-11)

Rulings taken 2026-11-11 (in `docs/system-brief.md`, sections 8 and 10): only a role's latest counting
verdict is read, so a checker's own later `APPROVED` closes its Blockers; verdicts carry the full sha;
lint reads the files git would commit.
Ruling on #14: the framework's tests must not depend on which checks are shipped, so adding a check
never breaks them.

Ruling on #17: the `kit` and `markers` lints read the kit in the tree (`.factory/`, or `kit/` in this
repository) and never the kit that runs them.

Wave 1 is closed: 2026-11-11, nine tickets, nine pull requests, every one verified on its head before
the merge gate merged it. The brief's test for the wave holds: every lint and `factory roles --check`
fails on each seeded defect and passes on a clean fixture (`bash scripts/gates.sh`).

Known and expected on `main`: `factory lint --root .` reports `kit FAIL` (the pipeline names roles that
wave 2 ships) and `references ERROR` (the documents cite example media ids and there is no manifest).

## Wave 2 progress

- [ ] #19 markers whose actor is not the role id (builder launched 2026-11-11)
- [x] dev kit: `launch.sh` posts every worker's exact prompt as `[ORCHESTRATOR] LAUNCH` on its issue or PR before it starts (`register-prompt.py`), and the verifier reads the builder's as acceptance criteria
- [x] wave 2 drafted: 20 tickets, dependency graph and 20 open questions in the state dir (`inputs/wave2/SUMMARY.md`)
- [ ] group A in flight: #21 (w2-01 production context), #22 (w2-02 GitHub adapter), #23 (w2-03 git state)
- [ ] group A to review and file: w2-04 harness adapter, w2-05 init, w2-06 sandbox profile, w2-16 checker and judge roles
- [ ] rule on Q1 to Q20 before group C; fix `kit/pipeline.json` (Q1) and `docs/harness.md` section 5 (Q14)
- [ ] groups B to E: review, file and launch as their dependencies merge

## Review

2026-11-11. Done: the proposal is in the tree; brief v1, `harness.md`, `resources.md`, the register and
the redrawn architecture are on `main`; wave 1 is filed as #1 to #7 with four builders launched.

Not done here, by design: nothing in `kit/` is built yet beyond the register (wave 1 is in flight), and
no account, key or login was created or changed. The owner's steps and decisions are in
`docs/resources.md`, sections 3 and 5.

What the research changed in the design:
- A third way to reach a resource. No platform offers an API key, so distribution uses OAuth grants
  that a program must keep fresh.
- TikTok may never allow unattended public posts, so the kit supports inbox and private posting.
- Fermi has no reduced access today, so workers reach it only through a local gateway.
- The subscription-login rule has an open terms question with each vendor (decision D1).
