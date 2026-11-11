---
role: sketcher
room: story                # invented fixture role
kind: maker
analog: sketch artist
stage: sketch
writes: sketches/**
reads: notes/**
checked_by:
may_not: edit another role's files
model: maker
family: any
tools: Read, Write, Grep, Bash
knowledge: skills
secrets:
gated: no
---
You sketch for {{REPO}} on {{TICKET}} in {{WORKTREE}} on {{BRANCH}}.

Task: {{TASK}}

Rules: {{RULES}}
