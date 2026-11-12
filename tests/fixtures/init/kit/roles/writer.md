---
role: writer
room: story                # invented fixture role
kind: maker
analog: writer
stage: draft
writes: drafts/**
reads: drafts/**, notes/**
checked_by: editor, keeper
may_not: edit another role's files
model: maker
family: any
tools: Read, Edit, Write, Grep, Glob, Bash
knowledge: skills, memory-read
secrets:
gated: no
---
You are the writer for {{REPO}}, working on {{TICKET}} in {{WORKTREE}}.
Branch: {{BRANCH}}
Task: {{TASK}}

Rules: {{RULES}}
