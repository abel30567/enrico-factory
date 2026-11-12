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

- [x] #19 markers whose actor is not the role id (merged in PR #20)
- [x] dev kit: `launch.sh` posts every worker's exact prompt as `[ORCHESTRATOR] LAUNCH` on its issue or PR before it starts (`register-prompt.py`), and the verifier reads the builder's as acceptance criteria
- [x] wave 2 drafted: 20 tickets, dependency graph and 20 open questions in the state dir (`inputs/wave2/SUMMARY.md`)
- [x] #24 (w2-00) `kit/pipeline.json` matches brief sections 6 and 16 (Q1; merged in PR #29)
- [x] #21 (w2-01) production context (merged in PR #28)
- [x] #22 (w2-02) GitHub adapter (merged in PR #31)
- [x] #23 (w2-03) git state (merged in PR #30)
- [x] #27 (w2-06) sandbox profile (merged in PR #32)
- [x] #25 (w2-04) harness adapter (merged in PR #35)
- [x] #36 (w2-07) ticket state (merged in PR #39)
- [x] #34 (w2-16) checker and judge roles (merged in PR #38)
- [x] #37 (w2-18) eval runner and the one prompt renderer (merged in PR #41)
- [x] #26 (w2-05) factory init (merged in PR #33)
- [x] #42 (w2-08) status, freeze and unfreeze (merged in PR #47)
- [x] #40 (w2-17) orchestrator, maker and analyst roles (merged in PR #49)
- [x] #45 (w2-12) blind pairs (merged in PR #50)
- [x] #44 (w2-10) launch prepare (merged in PR #52)
- [x] #43 (w2-09) lint --post (merged in PR #48)
- [x] #46 (w2-13) gate and ledger (merged in PR #51)
- [x] #57 (w2-19) story-room eval suites (merged in PR #59)
- [x] #60 (w2-11) launch finish (merged in PR #61)
- [x] #62 (w2-14) the eleven lock rules and `factory lock` (merged in PR #64)
- [x] #53 gate push retry: merged (PR #63, main 8b8e85e)
- [x] #67 w2-15 `factory tick`: merged (PR #68, verified in round 2, main 4fb7478, gates OK with 1010 tests)
- [x] #56 the amendment's `class` in the canon-keeper and canon-reviewer role files: merged (PR #70, main bc33542, gates OK)
- [x] #69 the sandbox denied an allowed endpoint on about one port in seven (the cause of the intermittent `test_connect_to_endpoint_allowed` failure; #54 closed as its duplicate): merged (PR #71, main be25902); the profile now uses an unfiltered deny plus an allow for Unix sockets
- [x] group D bodies reviewed against the built code: `body-11.md`, `body-14.md`, `body-19.md` in `~/.agent-orch/enrico-factory/inputs/wave2/`
- [x] #55 a counting `[BRAINTRUST] VOTE` fills the `bible` stage's judge place (merged in PR #58). Ruling: braintrust votes on every `bible` pull request; the tally is lock rule 6
- [x] rulings taken for group D (in the ticket bodies): Q7 to Q11, Q16, Q19 as recommended; the amendment is `bible/amendments/A-<n>.json` with a `class` key (the brief and `canon-keeper.md` over Q9's front matter); rule 6 applies to `amendment` stages and to `bible/**` changes outside the stage's own `paths`; a `LOCK` is posted as role `program:lock` with first line `[<ORCHESTRATOR ROLE ID>] LOCK`; `GATEWAY` and `RUN_TIMEOUT_MIN` stay in `production.env`, and `factory init` keeps writing `GATEWAY=on`, so a new production refuses launches until the gateway exists or it is set to `off` (fail closed; the w2-20 fixture sets `off`)
- [ ] follow-ups: #65 (a timeout BLOCKED can be posted while the worker is alive; also the worktree a timed-out checker leaves behind) waits for #77, which changes the same file; #74 (three places read a verdict or an actor more loosely than `markers.counts`) waits for #73 and needs a ruling on lock rule 6. #54 was closed as a duplicate of #69
- [x] update `docs/harness.md` section 5 (Q14): the result keys `addressed`, `selected`, `vote`, the per-marker rules and `VOTE`, as built in `kit/lib/harness.py`
- [x] group E: #72 w2-20 end to end: merged (PR #75, main 96124a6, gates OK with 1039 tests); a fixture ticket goes from LAUNCH to LOCK offline
- [ ] #73 a `[BRAINTRUST] VOTE` posted by the launcher never counts (`finish.py` writes the `reviewed head` line only for `markers.ROLE_VERDICTS`), so tick relaunches braintrust every tick on the `bible` and `reel` stages: PR #76 is READY, verifier V76 launched; the related drifts in the markers lint, lock rule 6 and the actor grammar are follow-up #74
- [ ] #77 (was follow-up #66) a merge that keeps failing makes tick post a LOCK and a void note on every tick and never asks the orchestrator (measured on main 96124a6): ticket filed with rulings (a void head is a `NEED`, `factory lock --apply` is the retry), builder queued behind the host limits (load was 11.9)
- [ ] wave 2 exit (brief section 17): `factory roles --check` prints OK on the shipped kit (done); a fixture ticket goes from LAUNCH to LOCK offline (done, #72); role evals have a measured noise floor (not done: `factory eval` reports `not measured` until the orchestrator runs the story-room suites live, three runs each)

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
