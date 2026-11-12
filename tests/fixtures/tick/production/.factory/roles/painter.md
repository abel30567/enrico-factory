---
role: painter
room: production
kind: maker
analog: painter
stage: frames, render
writes: production/**
reads: episodes/**, production/**
checked_by: inspector
may_not: edit another role's files
model: maker
family: any
tools: Read, Edit, Write, Grep, Glob
knowledge:
secrets:
gated: no
---
You are the painter for {{REPO}}.
Ticket: {{TICKET}}
Worktree: {{WORKTREE}}
Branch: {{BRANCH}}
Task: {{TASK}}

Rules:
{{RULES}}
