# CLAUDE.md

Read `docs/system-brief.md` first. It is the specification of what this repository builds.
`docs/architecture.md` shows the same system as diagrams: data flows, responsibilities, tools, and how
it uses GitHub issues and pull requests.

This repository (enrico-factory) builds the content factory. It is itself built with the software pattern below.
The content factory's own roles, stages and markers are in the brief, and are the product, not the way this repo is run.


## Development pattern: orchestrator + independent workers

Work is done by **headless agent workers**, one task each, in isolated git worktrees, coordinated
through **pull request comments**. Nothing merges until an independent verifier has signed off on the
exact head commit. Tooling: `.agents/orchestrator/` (`orch help`).

### Roles

| Role | Runs as | Does |
|---|---|---|
| **Orchestrator** | you + your interactive agent session | Triages tasks, gathers inputs workers cannot reach, launches workers, runs the merge gate, releases. The only role that merges. |
| **Builder** | `orch launch builder KEY slug "TASK"` | Own worktree and branch. Builds, runs every gate, opens the PR, posts `[BUILDER] READY`, exits. |
| **Verifier** | `orch launch verifier PR "criteria"` | Fresh detached worktree. Adversarial: re-runs the gates itself and tries to break every criterion. Never pushes. |
| **Fixer** | `orch launch fixer PR "focus"` | Addresses every finding, merges the base, posts `[BUILDER] ADDRESSED`. |
| **Blast radius** | `orch launch br PR "focus"` | Only for risky PRs (`orch risk PR`). Proves by running code what the change cannot break outside its diff. |

### PR comment markers (only a comment's FIRST line counts)

| Marker | By | Content |
|---|---|---|
| `[BUILDER] READY` | builder | head SHA, gate table, evidence (command → result) per acceptance criterion |
| `[VERIFIER] CHANGES REQUESTED` | verifier | `Reviewed head <sha>`, findings F1..Fn: criterion, evidence, repro command |
| `[BUILDER] ADDRESSED` | fixer | per finding: fix + commit SHA, or a reasoned rebuttal |
| `[VERIFIER] VERIFIED` | verifier | `Reviewed head <sha>`, evidence table for every criterion |
| `[BLAST-RADIUS] SAFE` / `UNSAFE` | blast radius | `Reviewed head <sha>`, the one fact that makes it safe and the command that proved it |
| `[BUILDER] BLOCKED` | builder/fixer | Missing / What was tried / Resume from (branch @ sha) |
| `[ORCHESTRATOR] …` | orchestrator | binding rulings, merge notes |

A verdict counts only if it is newer than the last READY/ADDRESSED and its `Reviewed head` is the
current head (or only the base branch was merged in since). After **3 verifier rounds** the
orchestrator decides; low-severity nits become `follow-up` issues instead of blocking.

### Rules

- One task = one worker = one branch = one worktree = one PR. Branch `feat/<KEY>-<slug>` from the base.
- Commits and PR titles start with `[KEY]`. The PR body says `Ref #<issue>`.
- Every worker runs the full GATES from `.agents/orchestrator/orch.env` and compares against the baseline.
  A failure that is not in the baseline is a finding, not "flaky".
- Run tasks in parallel only when their file sets are disjoint. Hot files (global styles, the main
  server/router file, lock files) go one lane at a time.
- When something is missing or ambiguous: do not guess and do not widen scope. Post `[BUILDER] BLOCKED`.
- **Merge only through `orch merge --apply`:** VERIFIED on the current head, SAFE if risky, head
  contains the current base, every CI check green, no `merge:hold`. One merge per run.
- Workers never merge, never push to protected branches, never skip hooks, never approve PRs, never post
  another role's marker, and never touch the orchestrator's resources (reserved ports, shared
  containers, logged-in browsers, the issue tracker, deploys). `hooks/worker-guard.mjs` enforces this.

### Definition of Done (verifier checklist)

- [ ] Every acceptance criterion checked with evidence (command → output).
- [ ] All gates green against the baseline.
- [ ] New behaviour has a test that fails without the change.
- [ ] Project rules in `.agents/orchestrator/project-rules.md` respected.
- [ ] No secrets, no unrelated changes, docs updated when a script, env var or workflow rule changes.

## This repository

- `GATES` = `bash scripts/gates.sh`: byte-compiles `kit/` and runs `tests/test_*.py` with the standard library `unittest`.
- Python 3 standard library, bash, `git` and `gh` only. No third-party packages.
- Tests never call GitHub or a model. They use recorded JSON fixtures under `tests/fixtures/`.
- `kit/` is project-agnostic: no Primal, Argelia or other production content in it. Sample material goes under `samples/`.
- Workers never run a model, a generator or a renderer against a real account, and never spend money.
- Harness files (`.agents/`, `scripts/gates.sh`, `CLAUDE.md`, `docs/system-brief.md`, `docs/architecture.md`) are showrunner-owned. Workers propose changes to them in a comment, not in a diff.
- This repository is public. Production material (lore, art, canon rulings, media) belongs in the production's own repository, never here. No machine paths, account names or credentials in any file.
