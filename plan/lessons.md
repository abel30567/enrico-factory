# Lessons

## 2026-11-11

- **The orchestrator directs; it does not do worker work.** I started reading card images and editing
  story files myself. Research, writing and checking go to role agents that post to GitHub. Before
  starting a task, ask which role owns it; if no role exists yet, building that role is the task.
- **The system is the product; the sample is the test.** I treated the Primal storyboard as the goal.
  The goal is the orchestration system. Sample work happens only through the system, to exercise it.
- **Load the research before designing.** After a context reset I began a design from summaries. Read
  the research files on disk first, and say plainly when something has not been re-read.

## 2026-11-13

- **The loop must not depend on one chat thread.** The wake thread sat wedged from 17:47 to 21:21 on
  2026-11-12 and two READY pull requests waited three hours for a verifier. Launches, the merge gate and
  the board sync belong to a program on a timer (`orch tick`, from a LaunchAgent). The wake only does what
  needs judgement, and a launch is a label (`status:ready`), not a command typed in a session.
- **A launchd job cannot read Desktop, Documents or Downloads.** The first install failed with
  "Operation not permitted". The heartbeat runs from a second checkout outside those folders.
- **A chat app's scheduled task cannot be a heartbeat.** The wake thread wedged a third time on 2026-11-13,
  from 21:20 to 22:07, with no background task involved: a message typed while a wake was running left one
  run open and eight wakes queued behind it. Moving launches to the tick was half the fix. The judgement wake
  is now also a program on a timer (`orch wake`): a fresh headless session per run, killed at a time limit,
  with GitHub and a notes file as its only memory. A chat thread is for talking to the owner, not for timing.
- **Do not push to `main` while a merge train is in its gates (2026-11-14).** A train tests `main` plus its
  pull requests for about 35 minutes and merges only if `main` has not moved. A docs commit pushed at 13:50Z
  ended the 13:39Z train of #439 and #440 with "main moved while GATES ran", and an earlier docs push did the
  same. Before any push, check `pgrep -f merge-gate.py`; while it runs, keep the commit local and push when
  the tick has ended.
- **A dependency is a blocker with a name, and blockers go first (2026-11-14).** The owner asked why so few
  tickets ran and found tickets waiting on a merge, on the next wake, and on dependencies they did not use:
  #154 waited for a TikTok app review although the four stages behind it use no platform, and a READY pull
  request in fermi-mcp sat 8 hours without a verifier because that repository has no tick. Work that can
  start now starts now. A ticket waits only for what it uses, the thing it waits for is an open ticket in
  its `Depends on` line, and each wake first clears the blockers that unlock the most tickets.
- **Before asking the owner for a key, measure whether a held key already reaches the service (2026-11-15).**
  #218 asked the owner to choose a rigging service, buy a plan and store its key, and it stood as the top
  blocker (28 tickets). The owner answered that rigging is the production house's work and part of the
  system under test. The rigging model was already hosted on the queue API of the key in `gen.model3d`: one
  real call rigged a sample model in 27 s. The ask came from the tool's hard-coded address, not from a need.
  An owner ask is only for what he alone can do: a decision, a consent, a payment no held account covers.
- **A leak scan prints a count and the distinct hits, never a cut list (2026-11-15).** Before pushing the
  recorded render session as a fixture, my first scan for machine paths and addresses printed its hits
  through `cut -c1-700`, and the cut hid a machine path in `out/render.json`. A second scan found it, and
  also a public DNS name, a MAC address and an attachment id that the driver's record mode had not scrubbed
  (#527). A scan for what must not be published ends in a number and the list of distinct matches
  (`grep -o ... | sort -u`), and zero is read from the count, not from a screen that looks clean.
- **Never `pkill` by pattern on this machine; end a process by its pid (2026-11-15).** To stop one orphan
  test run I typed `pkill -f 'unittest tests/test_' -u <uid> -P 1`. On macOS everything after the first
  pattern is another pattern, so `-u`, the uid, `-P` and `1` each matched on their own, and TERM went to
  every process of the owner's account with a `1` in its command line: the running tick, its merge gate
  and the train's gates, the local pollers of four fleet workers, the model proxy, the Fermi bridges, a
  tunnel, and helper processes of the owner's open apps. The train was restarted by the next tick four
  minutes later and the workers were re-attached by hand, but the apps were hit for nothing. A process is
  ended with `kill <pid>` after `ps -o pid,command -p <pid>` has shown what it is; `pgrep` only lists, and
  options go before the pattern.
- **A kit step done by hand is done whole, after reading its function (2026-11-15).** The sample
  production's first board stayed blocked after the kit was upgraded on its base: a role launched on a
  pull request reads the kit at the pull request's head, and the tick merges the base into a branch only
  when a lock is refused (#530). I merged the base into the branch by hand and pushed it, but the kit
  names a pull request's head by the branch in the production's clone, and its own step moves that
  branch before it pushes. Mine did not, so the next launch was given the old head and the showrunner
  answered BLOCKED a third time. Before doing by hand what a kit command does, read the function and do
  every effect it has; then file the gap, so that the hand is not needed twice.
