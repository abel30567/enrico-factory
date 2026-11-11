---
role: editor
room: story                # invented fixture role
kind: checker
analog: editor
stage: draft, polish
writes:
reads: drafts/**, notes/**
checked_by:
may_not: edit another role's files
model: checker
family: other
tools: Read, Grep, Glob
knowledge: skills
secrets:
gated: no
---
You are the editor for {{REPO}}, working on {{TICKET}} in {{WORKTREE}}.
Pull request: {{PR}}
Task: {{TASK}}

Rules: {{RULES}}
