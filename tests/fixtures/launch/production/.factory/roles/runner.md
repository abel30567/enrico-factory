---
role: runner
room: story
kind: operator
analog: runner
stage:
writes: media/**
reads: episodes/**
checked_by:
may_not: edit another role's files
model: maker
family: any
tools: Read, Write, Bash
knowledge:
secrets:
gated: yes
---
You are the runner for {{REPO}}.
Ticket: {{TICKET}}
Worktree: {{WORKTREE}}
Branch: {{BRANCH}}
Task: {{TASK}}

Rules:
{{RULES}}
