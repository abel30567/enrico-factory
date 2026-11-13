# Harness: how a role runs

Companion to `docs/system-brief.md` (section 14). Built: the launch of section 3, logins and the account
pool of section 4, the result object of section 5 and the sandbox of section 7, in `kit/lib/harness.py`,
`launch.py`, `finish.py` and `sandbox.py`, and `factory run` of section 8 with its tool interface, in
`kit/lib/cmd_run.py` and `kit/lib/tools/`, and the knowledge gateway of section 6 for the capabilities
`skills`, `memory-read` and `memory-write`, in `kit/lib/gateway.py`. Not built: the capabilities `search`
and `skills-write` (#282), any tool for `factory run` to call (none is installed yet), and forks of
section 9.
Machine-specific values (paths, account names, hosts) are never in this repository; they live in the
gitignored `production.local.env` of each production and are named in `docs/resources.md`.

## 1. The harness is a primitive

Every role runs on **hbar**, a Claude Code-compatible command-line agent. The factory treats it the
way a build treats a compiler:

- It is used only through its public surface: the flags and environment variables in section 3.
- The factory never patches it, never reads its internals and never depends on an unreleased build.
- Each production pins one harness build. Every `LAUNCH` marker records the harness version, so a
  result can be traced to the build that produced it.
- Everything that makes a role behave differently is outside the harness: the role file, the tools it
  is allowed, the knowledge it can read, the lints and judges that check it.

All harness-specific code is one adapter, `kit/lib/harness.py`: it turns a role file and a ticket into
a command line and reads the result back. Nothing else in the kit knows which harness runs.

The setting `HARNESS` names the pinned build's executable itself, by absolute path or with a leading
`~/`, never a wrapper script. The launcher builds the worker's environment from an allowlist and sets
the configuration directory, the token's file descriptor and, for the second family, the endpoint; a
wrapper that sets or clears any of them undoes that, and `--version` cannot show it. `factory doctor`
checks that `{HARNESS} --version` exits 0 and prints a version of the form `<n>.<n>.<n> (<name>)`, that
the state directory `STATE_DIR` is an absolute path outside the production that is or can be made a
writable directory, and that `SANDBOX` is `on` or `off`. That check cannot tell a wrapper from the build.

## 2. What the factory needs from a harness

Any harness that provides these eight things can run a role. They are the adapter's whole contract.

| # | Need | Used for |
|---|---|---|
| 1 | One headless run: prompt in, exit code out, no questions asked | every worker is detached |
| 2 | A tool allowlist per run | a role gets only the tools its file names |
| 3 | MCP servers set per run, and only those | the knowledge gateway is the one server a role sees |
| 4 | Model chosen per run | maker and checker slots; a different model family for `family: other` |
| 5 | A final answer that follows a JSON schema | the result object in section 5 |
| 6 | A login passed out of band, not in arguments or the environment | section 4 |
| 7 | User and machine settings ignored | a worker must not inherit the owner's hooks, plugins or servers |
| 8 | A version string | recorded in `LAUNCH` |

## 3. One launch

`factory launch <role> <ticket-or-pr>` does this, in order. A failure at any step launches nothing.

1. Read `kit/roles/<role>.md`. Check the ticket's stage names this role and the upstream stage is locked (for an episode stage after a sequence stage: locked for every sequence of the episode on the base branch).
2. For an operator: require a counting `[PRODUCER] GATE PASS` on the job's hash.
3. Create the worktree: a branch for a maker or operator, a detached checkout for a checker or judge.
   For a blind judge, `factory blind` has already replaced old and new with `A` and `B`.
4. Render the prompt from the role body and write it, with its sha256, outside the worktree.
5. Post `[<ORCHESTRATOR>] LAUNCH <role> <id>`: the prompt, its hash, model, harness version, base commit.
6. Start the knowledge gateway for this run with the role's `knowledge` capabilities (section 6), when
   `GATEWAY` is `on` and the capabilities give the role a tool. It stops when the run ends.
7. Start the harness inside the sandbox (section 7), detached:

```
<harness> -p
  --model <model of the role's slot>
  --tools <the role's tools>
  --strict-mcp-config --mcp-config <this run's gateway only>
  --setting-sources project
  --permission-mode bypassPermissions
  --output-format json --json-schema <result schema>
  < prompt
```

8. When it exits, the launcher (not the worker) finishes the job:
   - reads the result object and refuses a marker the role's kind may not post;
   - commits the changed files that match the role's `writes` globs, and lists any other changed path
     in the marker as dropped;
   - computes the head and the manifest hash from git itself;
   - pushes the branch, opens the pull request if there is none, and posts the marker.

A role of kind `orchestrator` is launched for a judgment only, and only when one is due: a ruling
after three rounds of notes (the number is a pull request) or a pitch selection (the number is a
ticket). It must be the orchestrator of the ticket's room. Steps 3 and 8 differ for it:

- It gets no git worktree and no branch. Its working directory is a plain directory with one `pr-<n>/`
  per pull request it judges, holding that pull's files at its head, and it is removed when the run ends.
- Its task quotes the comments it judges: the makers' and checkers' markers for a ruling, each pitch's
  report and counting verdicts for a selection.
- The launcher posts `RULING` on the pull request or `SELECTED #<pr>` on the ticket, and commits,
  pushes and opens nothing. A `SELECTED` that names a pull request outside the launch is `BLOCKED`.
- The newest `RULING` is quoted in the next maker's task and in the next checker's prompt.

A BLOCKED maker, a lint `ERROR` and a pivot are not launched (#242). Operators and analysts are refused.

A worker that dies, times out or returns no valid result gets `[<ROLE>] BLOCKED` posted for it with the
exit status. A missing result is never read as approval.

## 4. Inference: logins only

- Models are reached through subscription (OAuth) logins. No role and no program in the kit is given
  an inference API key, and the launcher clears any such variable before it starts a worker.
- The owner creates a long-lived inference-only token for each account once (the harness's
  `setup-token` command). Tokens are files outside every repository, readable by the launcher only.
- The launcher hands the token to the harness on a file descriptor. It is not in the arguments or the
  environment, so the tools a worker starts cannot read it. The token can run inference and nothing
  else: it cannot reach account settings or connectors.
- **Account pool.** `production.local.env` lists the accounts for each model slot. The launcher picks
  one per launch. When an account is out of usage the launch fails with a named reason, `factory tick`
  moves to the next account, and the ticket waits if none is left. It never falls back to an API key.
- **A second model family.** A role with `family: other` (judges and checkers, where one is available)
  runs on a model from a different vendor than its maker. It is still reached by login: a local proxy
  holds that vendor's subscription login and speaks the harness's API, and the adapter points the
  harness at it. When no second family is logged in, the launch records `family: same` in `LAUNCH`
  and the lab's judge-calibration loop is told, since same-family judging is a known weakness.
  The setting `PROXY_URL` in `production.local.env` is the proxy's URL: the adapter gives it to the
  harness as its endpoint for a role with `family: other`, the sandbox allows that port, and the
  proxy's client key is the file `proxy.client` in the secrets directory. There is no account list for
  this family. `factory doctor` connects to the URL's host and port and sends nothing.
- **Failure states the launcher must tell apart:** out of usage (wait or rotate), token expired or
  revoked (stop that account, raise a `resource:down` issue for the owner), proxy down (same).

## 5. The result object

A worker's last message is one JSON object. The harness enforces the schema; the launcher posts it.

```json
{
  "marker": "READY",
  "summary": "one paragraph",
  "evidence": [{"criterion": "…", "result": "…", "command": "…"}],
  "notes": [{"id": "B1", "severity": "Blocker", "text": "…", "evidence": "…"}],
  "addressed": [{"note": "B1", "by": "story-editor", "action": "changed", "text": "…"}],
  "prefers": {"choice": "A", "cited": ["panel 3"]},
  "selected": 12,
  "vote": {"for": 5, "against": 2, "reasons": ["…"]},
  "blocked": {"missing": "…", "tried": "…", "resume_from": "…"}
}
```

Only `marker` and `summary` are always required. No other key is allowed, and every key of a nested
object is required. The schema is `RESULT_SCHEMA` in `kit/lib/harness.py`; `harness.validate_result`
also applies these rules per marker:

| Marker | Needs |
|---|---|
| `READY` | at least one `evidence` entry |
| `ADDRESSED` | at least one `evidence` entry and one `addressed` entry (`action` is `changed` or `rebutted`) |
| `NOTES`, `SYMPTOMS` | at least one note; a `B<n>` id is a `Blocker`, an `I<n>` id an `Issue`; no id twice |
| `APPROVED` | no `Blocker` among its notes |
| `PREFERS A`, `PREFERS B`, `NO PREFERENCE` | `prefers.choice` of `A`, `B` or `none` to match |
| `SELECTED` | `selected`: the chosen pull request's number, 1 or more |
| `VOTE` | `vote` with `for` and `against` both 0 or more |
| `BLOCKED` | `blocked` with `missing`, `tried` and `resume_from` all filled in |

| Kind | Markers it may return |
|---|---|
| maker, operator | `READY`, `ADDRESSED`, `BLOCKED` |
| checker | `APPROVED`, `NOTES`, `BLOCKED` |
| judge | `PREFERS A`, `PREFERS B`, `NO PREFERENCE`, `BLOCKED` |
| analyst | `SYMPTOMS`, `BLOCKED` |
| orchestrator | `RULING`, `SELECTED`, `BLOCKED` |

A judge posting as `BRAINTRUST` may also return `VOTE`, its verdict on an amendment. `LAUNCH` is never a
result: the launcher posts it.

Because the launcher posts, a role cannot post another role's verdict, cannot name a head it did not
review, and holds no GitHub credential to misuse. `LOCK`, `[LINT]`, `[PRODUCER]` and `[PLATFORM-GATE]`
markers are posted only by the `factory` commands that compute them.

## 6. Knowledge: the Fermi layer

Fermi (an MCP server) is the studio's knowledge and memory. GitHub stays the state.

| Lives in Fermi | Lives in GitHub |
|---|---|
| Procedures: how to do a craft task, as skills | Every artifact, verdict, lock and ledger line |
| Craft knowledge and reference notes | The bible, the playbook, the rulebook, the registries |
| Recall: what happened on earlier tickets | Anything a lint, a gate or a lock reads |
| Search over indexed sources | |

Rules:

- **A memory advises; a file binds.** A role may be reminded by Fermi. It may cite only files. A
  lesson becomes binding when it is a playbook file locked by pull request; on that lock the launcher
  writes the lesson to Fermi as a skill or memory, so the two cannot disagree for long.
- **A role sees capabilities, not the server.** The role file's `knowledge` line names capabilities:

| Capability | Fermi tools behind it | Typical roles |
|---|---|---|
| `skills` | search and load skills | every role |
| `memory-read` | recall | makers, checkers, orchestrators |
| `memory-write` | write a memory, tagged with production, role and ticket | makers, analysts, orchestrators |
| `search` | search indexed sources | researcher, trend-scout, method-researcher |
| `skills-write` | create or change a skill | retro-agent, lab-director |

  Judges get none: a judge that can recall earlier rounds is no longer blind.

- **Nothing else is reachable.** Fermi's other tools (secrets, code execution, files, browser, the
  desktop bridge, messaging, schedules, cloud agents) are never exposed to a role.
- **Enforcement.** A full Fermi login exposes every tool, so no worker holds one. The launcher starts
  a **knowledge gateway** for each run: a small local MCP server that holds the Fermi login outside
  the sandbox, exposes only the role's capabilities, adds the production tag to every write and
  restricts every read to that production's tag plus the shared `studio` tag. It is built
  (`kit/lib/gateway.py`): a server on `127.0.0.1` in a thread of the launch supervisor, reached with a
  token made for the run and named in the run's `mcp.json` as the one server `knowledge`. Its tools
  are `skill_search` and `skill_load` (`skills`), `memory_recall` (`memory-read`) and `memory_write`
  (`memory-write`). A role that names only `search` or `skills-write` gets no server yet (#282).
- **Tags.** The production tag is the setting `REPO` in lower case; the shared tag is `studio`. A
  memory carries its tag in a first line the gateway writes, `[factory:<tag>] <role> #<ticket>`. A
  skill carries it as the keyword `factory:<tag>`. A result in another tag or in none is dropped and
  not mentioned, so the owner's own memories and skills never reach a role. A caller cannot pass a tag.
- **Settings.** `GATEWAY=on` (what `factory init` writes), `FERMI_URL` (the `https` address of Fermi's
  MCP endpoint) and the key file `knowledge.fermi` in `SECRETS_DIR`, which holds the key sent to Fermi
  as a bearer token. With `GATEWAY=on` and one of them missing, a launch of a role with a knowledge
  tool is refused: `the knowledge gateway is not usable: <reasons>`. With `GATEWAY=off` no server
  starts and `mcp.json` names none.
  The stronger form is a scoped token in Fermi itself with a data namespace per production. That
  changes the live Fermi server, so it is the owner's call (`docs/resources.md`, open decisions).
- **Fermi down is not a blocker.** Role prompts say so: with the gateway unreachable, a role works
  from the repository alone and says so in its result. Any failure of Fermi reaches the role as one
  answer, `knowledge: the knowledge server is unreachable; work from the repository alone and say so
  in your result`, and the run goes on.

## 7. Sandbox

A worker runs with every permission prompt off, so the boundary is the operating system, not the prompt.

| A worker can | A worker cannot |
|---|---|
| read the production repository | read tokens, key files or the launcher's state directory |
| write inside its own worktree and a scratch directory | write anywhere else, including the kit and other worktrees |
| reach the model endpoint and its knowledge gateway | reach GitHub with a credential, or any tool endpoint not named for its role |
| reach the web, if its role names a web tool | start schedulers, background services or other agents |

On macOS this is a `sandbox-exec` profile generated per launch; on a Linux render node, a container.
The profile is part of the kit and is tested the same way as a lint: each denied action has a test
that fails if the action succeeds.

The profile allows by default and denies by list. The kit's list covers the secrets directory, the
launcher's state directory and the credential stores it knows under the home directory, among them the
harness's own default configuration. A machine that holds other login stores (a second harness, a local
proxy's files) names them in the machine setting `SANDBOX_DENY`, a comma-separated list of absolute
paths, where a leading `~/` is the home directory. A launch refuses an entry that is relative, or that is
or contains the production, the worktree or the run directory. A worker's temporary directory is its own
`tmp` directory inside the run directory, not the launcher's.

Two limits are known. A deny list protects only what it names, so a new login store on the machine is
readable until it is added. And the kernel checks a program by its path, so a copy of a denied program
made by the worker still runs; for GitHub the boundary is that the worker cannot read the login.

## 8. Tools and credentials

- **Tools a role holds** are the harness's own (read, edit, write, search, shell, web) as its file lists.
- **Tools that cost money or cannot be undone** (generation, voice, render, upload) are never called
  by an agent directly. An operator writes a job file; `factory gate` passes or stops it; then
  `factory run <job>` makes the call with the credential and writes the outputs and the ledger line.
  The operator reads the outputs and reports. The credential never enters the worker.
- **Injected credentials** are the exception, for a tool an agent must drive step by step (the render
  node's remote control). The role file names the secret, the launcher injects only that one, only for
  a job with a gate PASS, and prefers a short-lived credential minted for the job.
- **Reads of other people's videos** (`docs/system-brief.md`, section 16) are made by `factory run`
  for `factory tick`, never by a worker. A read costs no money and changes nothing, so it needs no
  gate PASS; it is counted against a daily budget in `production.env` and refused past it. The
  answers go to the state directory with a read time and are refreshed or deleted within 30 days.
  At launch, `researcher` and `trend-scout` get a snapshot of the current reads and rankings of their
  ticket's topic in the working directory, outside git, and it is removed when the run ends (#269).
- Keys are files outside every repository, one per resource id in `kit/resources.json`.
  `factory doctor` checks that each exists and works, and prints names and states, never values.
- **The two stores** have a client each: `factory media` for the media store (public links, one
  manifest row per file) and `factory assets` for the asset store (private, an object is named by its
  sha256, every request is signed). Each reads its own key file, so a sandboxed worker can run neither.
- **Whose comments count.** No worker holds the GitHub login, but anyone who can comment on the
  repository can type a marker. The machine setting `GITHUB_LOGINS` in `production.local.env` names,
  comma-separated, the logins whose comments the kit reads; a comment by any other author is left out
  before a marker or a role is read from it. The setting must name the login the programs post as:
  with it empty no command reaches GitHub, and a post made as an unnamed login is an error.

## 9. When a harness is forked

The default answer to a failing role is never the harness. The order:

1. **The role**: its prompt, rubric, tools and knowledge. The lab's craft loop changes these.
2. **The checks**: a lint or a judge that is wrong.
3. **The inputs**: the upstream package was bad.
4. **The harness**, only when all of these hold:
   - the role's eval suite (`kit/evals/<role>/`) has failed its threshold in **three lab programs in a
     row**, each of which tried changes at levels 1 to 3;
   - the lab has a written cause in the harness, with a minimal reproduction that does not involve the
     role's prompt (for example: the context is cut before the storyboard is read; a tool result is
     dropped; a schema is not enforced);
   - the same reproduction fails on the pinned build and no harness setting fixes it.

Then:

- The lab director opens a `harness-fork` issue with the evidence. The producer gate checks the three
  conditions; no one argues a fork into existence.
- The fork is a separate repository, named for the one process it serves (for example
  `hbar-storyboard`). It serves **that process only**: every other role stays on the primitive.
- A **dedicated engineering team** owns it: its own orchestrator, builders and verifiers, run with the
  software pattern this repository is built with. Its single metric is the failing role's eval suite,
  which it cannot edit. It keeps a change only if the eval beats the best result by more than the
  noise, and logs every experiment.
- The fork tracks upstream: it merges each new primitive build and re-runs the eval. When the
  primitive passes the eval by itself, the fork is retired and the process moves back.
- No per-role setting such as `HARNESS_<ROLE>` is read today: `HARNESS` is the one harness of every
  role. A setting that names a fork per role comes with the first fork.

A fix that would help every process is not a fork: it is an issue on the harness's own repository,
handled by that project's own loop.
