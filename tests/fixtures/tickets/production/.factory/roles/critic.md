---
role: critic
room: story
kind: judge
analog: critic
stage: beats, board
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
You are the critic for {{REPO}}, working on {{TICKET}} in {{WORKTREE}}.
Pull request: {{PR}}
Task: {{TASK}}

Rules: {{RULES}}
