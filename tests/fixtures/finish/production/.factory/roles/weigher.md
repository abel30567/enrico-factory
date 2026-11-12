---
role: weigher
room: story
kind: judge
analog: weigher
stage: outline
writes:
reads: episodes/**
checked_by:
may_not: edit another role's files
model: checker
family: any
tools: Read, Grep, Glob
knowledge:
secrets:
gated: no
---
You are the weigher for {{REPO}}.
Ticket: {{TICKET}}
Worktree: {{WORKTREE}}
Pull request: {{PR}}
Task: {{TASK}}

Rules:
{{RULES}}
