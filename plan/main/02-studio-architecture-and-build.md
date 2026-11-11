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

- [ ] #1 dispatcher, front matter, roles, formats, story pipeline (PR #9: fixer done, round 2 review)
- [ ] #2 markers and verdict freshness (PR #7: fixer done, round 2 review)
- [x] #3 lint framework, provenance, references, media (PR #8, merged 2026-11-11)
- [ ] #4 structure lint (waits on #1)
- [ ] #5 kit and markers lints (waits on #1, #2)
- [ ] #6 canon and spoiler lints (builder launched 2026-11-11)
- [x] #10 `factory doctor` (PR #12, merged 2026-11-11)
- [ ] #11 follow-up: lint reads the files git would commit (after #3)
- [ ] #13 follow-up: doctor, a command that cannot start is a failed check

Rulings taken 2026-11-11 (in `docs/system-brief.md`, sections 8 and 10): only a role's latest counting
verdict is read, so a checker's own later `APPROVED` closes its Blockers; verdicts carry the full sha;
lint reads the files git would commit.

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
