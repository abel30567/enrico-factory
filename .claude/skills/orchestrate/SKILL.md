---
name: orchestrate
description: Run this repository's orchestrator loop — turn tasks into GitHub issues, launch builder / verifier / fixer / blast-radius workers in parallel worktrees, read their PR comment markers, and merge through the merge gate. Use when the user says "orchestrate", "launch a builder for X", "what's the status of the workers", "run the merge gate", "verify PR N", "fan out these tasks", or asks to work several tasks in parallel.
---

# Orchestrate

You are the **orchestrator**: the only role that merges. Workers are headless agents started by
`.agents/orchestrator/orch`; they coordinate only through first-line PR comment markers. Read the
"Development pattern" section of CLAUDE.md / AGENTS.md first.

`O=.agents/orchestrator/orch`

## Loop

1. **Status first.** `$O status` — live workers, each open PR's builder/verifier/blast-radius state
   and its `next=` step. Act on `next=`; never on a worker's exit code.
2. **Triage a task** before any launch:
   - Make sure it has an issue: `$O task "summary" body.md` (key `ISSUE-<n>`), or an existing `[KEY]` issue.
   - Write TASK.md: context, scope (files; out of scope), numbered acceptance criteria each checkable by a
     command, ground truth (real data the result must match), verification commands.
   - Gather anything workers cannot reach (designs, tracker text, production data, logged-in browsers)
     into `~/.agent-orch/<repo>/inputs/<KEY>/` and reference those paths in TASK.md.
   - Unclear or blocked tasks stay in Backlog. Ask the user rather than launching a guess.
3. **Partition.** Launch in parallel only tasks with disjoint file sets. Space launches ~20 s apart.
4. **Launch**: `$O launch builder KEY slug "$(cat TASK.md)"`.
5. **When a `[BUILDER] READY` appears**: `$O launch verifier PR "<acceptance criteria + ground truth>"`.
   If `$O risk PR --reasons` prints anything, also `$O launch br PR "<what could break outside the diff>"`.
6. **CHANGES REQUESTED** → `$O launch fixer PR "address F1..Fn"`, then a FRESH verifier. After 3 rounds,
   rule yourself with an `[ORCHESTRATOR]` comment: nits → `follow-up` issues; anything that can cause a
   false pass or a regression keeps blocking.
7. **Merge**: `$O merge` (dry run), then `$O merge --apply`. One PR per run; repeat after each merge.
   `BEHIND` means it merged the base in and CI must re-run; run it again later.
8. **Report** to the user: a table of KEY | PR | state | next step, plus anything that needs a human.

## Never

- Merge with `gh pr merge` directly, or merge on red / pending / missing checks.
- Trust a worker's gate table without a verifier re-running it.
- Run `orch launch` while `orch status` shows FREEZE on; ask the user.
- Paste secrets into TASK.md, PR comments or issues.
