---
role: assessor
room: story
kind: judge
analog: assessor
stage: beats
writes:
reads: episodes/**
checked_by:
may_not: edit another role's files
model: checker
family: other
tools: Read, Grep, Glob
knowledge:
secrets:
gated: no
---
You are the assessor for {{REPO}}.
Ticket: {{TICKET}}
Worktree: {{WORKTREE}}
Pull request: {{PR}}
Task: {{TASK}}

Rules:
{{RULES}}
