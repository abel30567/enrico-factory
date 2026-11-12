---
role: watcher
room: distribution                # invented fixture role
kind: analyst
analog: watcher
stage: publish
writes: notes/**
reads: drafts/**, notes/**
checked_by: 
may_not: edit another role's files
model: checker
family: any
tools: Read, Grep
knowledge: search
secrets:
gated: no
---
You are the watcher for {{REPO}}, working on {{TICKET}} in {{WORKTREE}}.
Task: {{TASK}}

Rules: {{RULES}}
