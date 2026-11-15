
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
- The master first (the owner's order of 2026-11-15). The goal in front of every other is one master video
  out of the sample production: the story room's locks, then the production room on the engine, up to the
  pilot's conform (#158). Platform work (#449, #159, #203, #205, #206, #213, #136) keeps its tickets but is
  not asked of the owner and takes no wake time while a ticket on the way to #158 can move. What the
  production house makes (models from images, rigs, levels, renders, sound) is never asked of the owner:
  before any ask for a key or an account, measure whether a key the production already holds reaches the
  same service, as #218 did for rigging.
