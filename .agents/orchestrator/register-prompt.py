# register-prompt.py ROLE ID KIND NUMBER PROMPT_FILE MODEL WT BASE [--note TEXT] [--dry-run]
# Register a worker's exact input prompt on GitHub BEFORE the worker starts:
# every agent the orchestrator makes leaves its full prompt on its issue (builder) or PR (verifier,
# fixer, blast radius) as an `[ORCHESTRATOR] LAUNCH` comment. launch.sh refuses to start a worker
# whose prompt could not be registered. The comment is a registration, not a ruling: no gate reads it.
#   KIND = issue | pr. Token-shaped strings are redacted (the count is shown) and the home directory is
#   written as ~ (this repository is public); the sha256 is of the text the worker receives. Prompts
#   longer than one comment are split into numbered parts. --dry-run prints instead of posting.
import datetime as dt, hashlib, re, subprocess, sys
from pathlib import Path

LIMIT = 60000  # GitHub caps a comment at 65536 characters; leave room for the header
SECRETS = [
    r"gh[opsur]_[A-Za-z0-9]{20,}", r"github_pat_[A-Za-z0-9_]{20,}", r"sk-ant-[A-Za-z0-9_-]{20,}",
    r"\bsk-[A-Za-z0-9]{32,}\b", r"xox[abprs]-[A-Za-z0-9-]{10,}", r"\bAKIA[0-9A-Z]{16}\b",
    r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{20,}=*", r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
]


def main(a):
    dry = "--dry-run" in a
    note = a[a.index("--note") + 1] if "--note" in a else ""
    role, wid, kind, num, pfile, model, wt, base = [x for x in a if x != "--dry-run"][:8]
    if kind not in ("issue", "pr") or not num.isdigit():
        sys.exit(f"register-prompt: need KIND issue|pr and a number, got {kind} {num!r}")
    raw = Path(pfile).read_text()
    home = str(Path.home())
    text, hits, wt = raw.replace(home, "~"), 0, wt.replace(home, "~")
    for pat in SECRETS:
        text, n = re.subn(pat, "[REDACTED]", text)
        hits += n
    digest = hashlib.sha256(raw.encode()).hexdigest()
    fence = "`" * max(3, 1 + max((len(m) for m in re.findall(r"`+", text)), default=0))
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    parts = [text[i:i + LIMIT] for i in range(0, len(text), LIMIT)] or [""]
    bodies = []
    for i, part in enumerate(parts, 1):
        n = f" (part {i}/{len(parts)})" if len(parts) > 1 else ""
        bodies.append(
            f"[ORCHESTRATOR] LAUNCH {role} {wid}{n}\n"
            f"Model `{model}` · base `{base}` · worktree `{wt}` · {ts}\n"
            f"Prompt sha256 `{digest}` · {len(raw)} chars · {hits} redaction(s). The worker receives exactly this text,"
            f" with `~` expanded{' and redactions aside' if hits else ''}.{(' ' + note) if note else ''}\n\n"
            f"<details><summary>Input prompt{n}</summary>\n\n{fence}text\n{part}\n{fence}\n\n</details>\n")
    for b in bodies:
        if dry:
            print(b); continue
        p = subprocess.run(["gh", kind, "comment", num, "--body-file", "-"], input=b, capture_output=True, text=True)
        if p.returncode:
            sys.exit(f"register-prompt: gh {kind} comment {num} failed: {p.stderr.strip()[:300]}")
    print(f"registered {role} {wid} prompt on {kind} #{num} ({len(bodies)} comment(s), sha256 {digest[:12]})")


if __name__ == "__main__":
    main(sys.argv[1:])
