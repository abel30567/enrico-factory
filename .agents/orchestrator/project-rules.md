<!-- project-rules.md — pasted into EVERY worker prompt as PROJECT RULES. Keep it short and
     checkable; the verifier uses it as a checklist. Delete the examples that do not apply. -->
- Follow the conventions in the repository's CLAUDE.md / AGENTS.md and in the README of every folder you touch.
- Do not add dependencies unless the task says so.
- Every behaviour change ships with a test that fails without the change.
- Never commit secrets, `.env` files or credentials. Use the existing config/env mechanism.
- Do not edit CI, deploy or infrastructure config unless the task is about it.
- Python 3 standard library only. Tests use `unittest`, live in `tests/test_*.py`, and never call GitHub or a model (use fixtures in `tests/fixtures/`).
- `kit/` is project-agnostic: no Primal, Argelia or other production content in it.
- `docs/system-brief.md` is the specification. Where the task and the brief disagree, post `[BUILDER] BLOCKED`; do not pick one.
- Do not change `.agents/`, `scripts/gates.sh`, `CLAUDE.md`, `README.md`, `docs/`, `kit/resources.json` or `plan/`.
- No inference API key anywhere: no code, test or document may read, require or mention one as a way to run a role.
- Only `kit/lib/harness.py` may know which agent harness runs. No other file names a harness flag.
- This repository is public: no production material (lore, art, rulings, media), machine paths, account names or credentials.
<!-- Examples of project-specific rules:
- UI sizes use rem on a 10px root; no raw hex colours, use the design tokens.
- Database changes need a reversible migration.
- Public API changes need a changelog entry.
-->
