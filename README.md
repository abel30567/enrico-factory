# enrico-factory

A content factory: a system that runs story production the way a studio does, with agents in the
roles and GitHub as the only state. Research, canon, story, script and storyboard now; 3D build,
render, edit and QC later.

It is the content counterpart of an orchestrator / builder / verifier pattern for software. A
showrunner files tickets as issues, makers deliver one artifact per pull request, checkers give notes
on the exact commit, and a merge is a lock.

- `docs/architecture.md`: diagrams of the data flows, responsibilities, tools and the GitHub model.
- `docs/system-brief.md`: the specification.
- `docs/research/`: the research behind it.
- `kit/`: the system itself (roles, formats, pipeline, command line). Not built yet; it is built by
  agent workers through pull requests.
- `samples/primal/`: the first sample production.
- `.agents/orchestrator/`: the software pattern this repository is built with.

Status (2026-11-11): design only. The architecture and brief are written; wave 1 of the build has not
started. See `docs/system-brief.md` section 11 for the build order.
