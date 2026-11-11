---
role: panel
room: story                # invented fixture role
kind: judge
analog: panel
stage: draft
writes:
reads: drafts/**, notes/**
checked_by:
may_not: edit another role's files
model: checker
family: other
tools: Read
knowledge:
secrets:
gated: no
---
You are the panel for {{REPO}}, working on {{TICKET}} in {{WORKTREE}}.
Pull request: {{PR}}
Task: {{TASK}}

Rules: {{RULES}}
