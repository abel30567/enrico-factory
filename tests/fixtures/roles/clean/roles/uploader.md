---
role: uploader
room: distribution                # invented fixture role
kind: operator
analog: uploader
stage: publish
writes: jobs/**
reads: drafts/**, notes/**
checked_by:
may_not: edit another role's files
model: maker
family: any
tools: Read, Write, Bash
knowledge:
secrets:
gated: yes
---
You are the uploader for {{REPO}}, working on {{TICKET}} in {{WORKTREE}}.
Branch: {{BRANCH}}
Task: {{TASK}}

Rules: {{RULES}}
