
Orders for this project (enrico-factory):

- Also read `plan/lessons.md`. The spec is `docs/system-brief.md` (build order in section 17), with
  `docs/harness.md` and `docs/resources.md`.
- You direct: never write under `kit/` or `tests/` yourself. Anything there is a ticket for a builder.
- `merged #N`: tick `plan/main/02-studio-architecture-and-build.md` and commit it.
- A full ticket also has a seeded defect plus a clean fixture for every check.
- The open list of what waits for the owner is in `docs/resources.md`.
- Blockers first (the owner's order of 2026-11-14). Work that can start now starts now: it does not wait
  for a merge, a timer or the next wake. A ticket waits only for what it uses, and what it waits for is an
  open ticket named in its `Depends on` line. At the start of each wake, rank the open tickets that have no
  open dependency by how many open tickets they unlock, and act on the top of that list before anything
  else. An owner item on the list gets its exact ask posted in the same wake. A dependency the ticket does
  not use is removed by a ruling, as #154 was split into #154 and #449.
