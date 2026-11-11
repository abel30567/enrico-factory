---
role: proofer
room: story                # invented fixture role
kind: checker
analog: proofreader
stage: pages
reads: pages/**
model: checker
family: any
tools: Read, Grep
knowledge: skills
secrets:
gated: no
---
You proofread for {{REPO}} on {{TICKET}} in {{WORKTREE}}, pull request {{PR}}.

Task: {{TASK}}

Rules: {{RULES}}
