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
- [x] follow-up #65 (a timeout BLOCKED can be posted while the worker is alive; also the worktree a timed-out checker leaves behind) Filed as #81 on 2026-11-12 (body measured at 3257e4f; #65 closed as replaced). The builder was launched on 2026-11-12 when load dropped; PR #193 was verified and merged on 2026-11-12 (main c021e65, gates OK, 1084 tests). Rulings: a run is dead after 2 x `RUN_TIMEOUT_MIN` plus 10 min (the supervisor's worst case, since only it posts a marker), and tick removes a timed-out checker's worktree as the supervisor does. #54 was closed as a duplicate of #69, #66 became #77 and #74 became #79
- [x] update `docs/harness.md` section 5 (Q14): the result keys `addressed`, `selected`, `vote`, the per-marker rules and `VOTE`, as built in `kit/lib/harness.py`
- [x] group E: #72 w2-20 end to end: merged (PR #75, main 96124a6, gates OK with 1039 tests); a fixture ticket goes from LAUNCH to LOCK offline
- [x] #73 a `[BRAINTRUST] VOTE` posted by the launcher never counted, so tick relaunched braintrust every tick on the `bible` and `reel` stages: merged (PR #76, main d61242a, gates OK with 1050 tests)
- [x] #77 (was follow-up #66) a merge that keeps failing made tick post a LOCK and a void note on every tick and never asked the orchestrator: merged (PR #78, verified in round 1, main 3257e4f, gates OK with 1057 tests). Tick now prints `NEED #<n> lock refused: merge failed at <head>` on a void head; `factory lock --apply` is the orchestrator's retry
- [x] #79 (was follow-up #74) the markers lint, lock rule 6 and the role lint read a verdict or an actor as `markers` does: PR #80 came back BLOCKED at 8c6b212 (three existing tests the ticket did not name read pull 10's vote, two of them in files outside Scope). Ruled on the PR: the kit stays as built, the three expectations change, Scope grows by `tests/test_cmd_lock.py` and `tests/test_vote_counts.py`; fixer launched. Ruling on rule 6: a vote must be for the current head (a later commit that is not a merge of the base voids it), and rule 6 does not ask the role condition of `markers.counts` Verified at e5877e6 and merged as PR #80 (main a8025c7) on 2026-11-12.
- [ ] wave 2 exit (brief section 17): `factory roles --check` prints OK on the shipped kit (done); a fixture ticket goes from LAUNCH to LOCK offline (done, #72); role evals have a measured noise floor (not done). The owner said on 2026-11-12 to use the logins already held, so step 7 of `docs/resources.md` section 3 is done on this Mac: `~/.config/enrico-factory/secrets/` holds `inference.primary/kayo`, `tars`, `kase` (the hbar loop's account, so last in the pool) and `proxy.client`; the proxy answers with that key and lists the Grok models, so the xAI login is live. Next: a scratch production with `production.local.env` pointing there, `factory doctor`, one real case as a smoke test, then `factory eval --harness real --runs N` per story-room role for the noise floor, when load is under 9. This also unblocks wave 3)

## Roadmap registered (2026-11-12)

- [x] one GitHub issue per work item for the rest of wave 2 and for waves 3 to 7 (#82 to #219), each under its wave's milestone with a `Depends on:` line of issue numbers. Labels: `tracked` and `roadmap` on all (an item is a launchable ticket once it also has `status:ready`), `owner` on the 17 owner steps, `who:builder`, `who:orchestrator` or `who:factory` on the rest. The breakdown and the id map are in the state dir (`inputs/roadmap/roadmap.json`, `issues.json`). The questions the documents leave open are #220; each is ruled there before the item it decides is launched
- [x] milestone target dates, as estimates to revise at each wave exit: wave 2 2026-11-13, wave 3 2026-11-19, wave 4 2026-11-28, wave 5 2026-12-10, wave 6 2027-01-07, wave 7 2027-02-18. Waves 4 onward wait on the engine smoke test; waves 6 and 7 carry fixed 7-day audience windows
- [x] #82 (w2-22) a scratch production made by `factory init` passes `factory doctor --wave 2` on this Mac: `harness`, `inference.primary`, `inference.other`, `state.github` ok; `knowledge.fermi` missing until the gateway (#88). `HARNESS` must be the pinned binary, not the wrapper. Token expiry, upper bounds from file dates plus one year: kayo 2027-10-08, tars and kase 2027-11-07. The run found ten defects, filed as two tickets with builders launched on 2026-11-12: #221 (doctor says ok for settings a launch cannot use; merged as PR #223, main be5aa3d, gates OK with 1123 tests, and doctor on the scratch production now prints 4 ok, 0 missing) and #222 (the sandbox profile leaves login stores outside the kit's list readable). After both merge: the orchestrator updates `docs/harness.md` section 7 and `docs/resources.md` line 47, and a small ticket adds `SANDBOX_DENY=` to the example file. #222 must merge before the first real launch (#85)
- [ ] #83 (w2-23) every `Gh` method ran once against GitHub on the private scratch repository `abel30567/enrico-factory-scratch`. The read shapes agree with `Recorded`; verdict not ready, 14 findings. `Gh.merge` (findings 1 to 4, two high: the local base stays behind GitHub after a merge, and a failed fetch after the server merge makes `factory lock` post "void" on a merged pull) is one builder ticket, in drafting; no real production locks a pull before it merges. The adapter differences and the comment-role trust are #225 (after the merge ticket, same file); scale past 1,000 issues is #226
- [x] #212 (decision D3) the owner's direction on 2026-11-12: renders run on short-lived cloud GPU nodes in the existing AWS account, started per job by the factory's render tool (#117), not through the Fermi fleet (which allows only `t3` types and would need a change to the live server). The account has an on-demand GPU quota; spot is 0. Still needed from the owner: a yes to a render-only key (#204) and a spend cap (#214). The Epic account exists (#207); linking GitHub and the download are the orchestrator's, in #114

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
