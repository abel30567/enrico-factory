"""The release of an episode: ``distribution/<ep>/release.json`` on the base, read by lock rule 11.

An output named ``release.json`` releases the episode of the job's ticket. After a DONE call `factory run` commits
its bytes on the base as ``distribution/<ep>/release.json``, unless the base already has that file. Only a tool
that has made a post public writes it.
"""

from kit.lib import gitstate, ledger, lockrules, production, tickets

OUTPUT = "release.json"


class ReleaseError(ledger.LedgerError):
    pass


def write(p, gh, request, job_dir, outputs) -> bool:
    """Commit the run's ``release.json`` output on the base as ``factory`` and push; whether it was written."""
    if not any(item["name"] == OUTPUT for item in outputs):
        return False
    episode = tickets.episode(request["ticket"]["unit"])
    digest, root, base = request["hash"], p.root, production.base(p)
    path = lockrules.RELEASE.format(episode)
    if gitstate.show(root, base, path) is not None:
        return False
    try:
        branch = gitstate.git(root, "symbolic-ref", "--short", "HEAD").strip()
    except gitstate.GitError:
        branch = None
    if branch != base or gitstate.git(root, "diff", "--cached", "--name-only").strip():
        raise ReleaseError(f"the checkout is not on {base} with a clean index")
    text = (job_dir / "out" / OUTPUT).read_text(encoding="utf-8")
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    gitstate.commit(root, [path], f"release: {episode} job {digest[:12]}", "factory")
    gh.push(root, base)
    return True
