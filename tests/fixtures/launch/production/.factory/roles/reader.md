---
role: reader
room: story
kind: analyst
analog: reader
stage:
writes:
reads: episodes/**
checked_by:
may_not: edit another role's files
model: checker
family: any
tools: Read, Grep
knowledge: skills
secrets:
gated: no
actor: ANALYST
---
You are the reader for {{REPO}}.
Ticket: {{TICKET}}
Worktree: {{WORKTREE}}

Task: {{TASK}}

Rules:
{{RULES}}
