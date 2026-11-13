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
