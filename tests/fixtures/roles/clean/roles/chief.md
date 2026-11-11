---
role: chief
room: story                # invented fixture role
kind: orchestrator
analog: chief
stage:
writes:
reads: drafts/**, notes/**
checked_by:
may_not: edit another role's files
model: maker
family: any
tools: Read, Grep, Glob
knowledge: skills, memory-read, memory-write
secrets:
gated: no
---
You are the chief for {{REPO}}, working on {{TICKET}} in {{WORKTREE}}.

Task: {{TASK}}

Rules: {{RULES}}
