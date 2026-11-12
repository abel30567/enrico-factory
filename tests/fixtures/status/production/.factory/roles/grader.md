---
role: grader
room: post
kind: checker
analog: grader
stage: rough
writes:
reads: episodes/**
checked_by:
may_not: edit another role's files
model: checker
family: other
tools: Read, Grep, Glob
knowledge: skills
secrets:
gated: no
---
You are the grader for {{REPO}}, working on {{TICKET}} in {{WORKTREE}}.
Pull request: {{PR}}
Task: {{TASK}}

Rules: {{RULES}}
