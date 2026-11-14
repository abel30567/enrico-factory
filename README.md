# enrico-factory

A content factory: an animation studio run entirely by agents, with GitHub as the only state. Five
rooms (story, production in Unreal Engine, post, distribution, and an autoresearch lab) each have
their own orchestrator and hand work on only as locked packages. No human approves anything inside
the loop.

It is the content counterpart of an orchestrator / builder / verifier pattern for software. An
orchestrator files tickets as issues, makers deliver one artifact per pull request, a deterministic
lint and an independent judge check the exact commit, and a merge is a lock.

- `docs/system-brief.md`: the specification.
- `docs/architecture.md`: diagrams of the data flows, responsibilities, tools and the GitHub model.
- `docs/harness.md`: how a role runs on the agent harness, and when a harness is forked.
- `docs/resources.md`: every resource a run needs, what exists and what is missing.
- `docs/design/autonomous-story-studio.html`: the proposal the specification adopts.
- `docs/research/`: the research behind it.
- `kit/`: the system itself (roles, formats, pipeline, command line). It is built by agent workers
  through pull requests.
- `samples/primal/`: the first sample production.
- `.agents/orchestrator/`: the software pattern this repository is built with.

Status (2026-11-14): wave 1 of the build is closed and later waves are being built. Sixteen role
eval suites have a measured noise floor in `docs/eval-floors.tsv`. See `docs/system-brief.md` section
17 for the build order and the open issues for what is being built.
