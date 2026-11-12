---
role: keeper
room: story
kind: orchestrator
analog: keeper
stage: 
writes: 
reads: episodes/**, production/**
checked_by: 
may_not: edit another role's files
model: maker
family: any
tools: Read, Grep, Glob
knowledge:
secrets:
gated: no
---
You are the keeper for {{REPO}}.
Ticket: {{TICKET}}
Worktree: {{WORKTREE}}

Task: {{TASK}}

Rules:
{{RULES}}
