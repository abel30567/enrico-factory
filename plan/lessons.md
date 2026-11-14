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
