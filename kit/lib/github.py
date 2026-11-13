"""The one adapter to GitHub, the only state (system brief sections 7, 8 and 14).

Two forms with the same methods:
- ``Recorded``: a directory of JSON, read and written. Every test and the offline fixture use it.
- ``Gh``: a thin wrapper around ``gh`` and ``git``, run live only by the orchestrator.
``connect`` is the only place that chooses. No other file in the kit calls ``gh``.

``merge`` merges on GitHub and ``sync_base`` brings the clone's local base up to GitHub's; after both, the
local base, ``HEAD`` (when the base is checked out) and the working tree hold the merge on either form.
``Gh.merge`` starts no git; ``Recorded.merge`` merges in the clone, so ``Recorded.sync_base`` only reads.

Shapes (keys exactly these; times ``YYYY-MM-DDTHH:MM:SSZ``):
- issue: ``{"number", "title", "body", "state": "open"|"closed", "labels": [sorted names], "created_at"}``;
- pull: ``{"number", "title", "body", "state": "open"|"merged"|"closed", "labels", "head_ref", "base_ref",
  "ticket": int | None, "created_at", "merged_at": str | None}``;
- comment: ``{"id": int, "created_at", "author": str, "role": str, "body"}``.

``author`` is the login that posted a comment. ``comments`` leaves out every comment whose author the
setting ``GITHUB_LOGINS`` does not name (compared without regard to case), so no caller ever reads one;
``Gh.post`` raises when GitHub says the kit posted as such a login. ``Gh`` names nobody by default.
``Recorded`` always names ``factory``, and reads a stored comment with no ``author`` as written by it.

GitHub records no role, so ``Gh.post`` ends the body with ``<!-- factory:role <role> -->`` after a
newline. That newline cannot be told apart from one the body already had, so a posted comment's body
always comes back ending with a newline, in both forms.

Neither form reads or prints a credential, and an error message never holds a command's stderr; the one
exception is the three digits of an HTTP status, as in ``post #1: exit 1 (HTTP 403)``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

TICKET = re.compile(r"^Ticket: #(\d+)\s*$")
ROLE = re.compile(r"^[a-z0-9:-]+$")
ROLE_LINE = re.compile(r"^<!-- factory:role ([a-z0-9:-]+) -->$")
ISSUE_STATES = ("open", "closed", "all")
PULL_STATES = ("open", "closed", "merged", "all")
ISSUE_FIELDS = "number,title,body,state,labels,createdAt"
PULL_FIELDS = "number,title,body,state,labels,headRefName,baseRefName,createdAt,mergedAt"
ISSUE_VIEW_FIELDS = ISSUE_FIELDS + ",url"
GIT_IDENTITY = ("-c", "user.name=factory", "-c", "user.email=factory@factory.invalid")
REPO_NAME = re.compile(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+")
FACTORY_LOGIN = "factory"
LABEL_MAX = 50
BODY_MAX = 65536
HTTP_STATUS = re.compile(r"\(HTTP (\d{3})\)$")
NOT_RESOLVED = "GraphQL: Could not resolve to "
TLS_TIMEOUT = "TLS handshake timeout"
RUNS = 3


class GitHubError(Exception):
    """A GitHub operation failed: the operation, the issue or pull number and an exit status."""


def connect(settings, env=None):
    """``Recorded`` when ``FACTORY_GITHUB`` names a directory, else ``Gh`` on ``settings["REPO"]``.

    Both read the comments of the logins ``settings["GITHUB_LOGINS"]`` names, comma-separated.
    """
    env = os.environ if env is None else env
    logins = tuple(name.strip() for name in settings.get("GITHUB_LOGINS", "").split(",") if name.strip())
    directory = env.get("FACTORY_GITHUB", "")
    if directory:
        if not Path(directory).is_dir():
            raise GitHubError("connect: FACTORY_GITHUB is not a directory")
        return Recorded(Path(directory), logins)
    repo = settings.get("REPO", "")
    if not repo:
        raise GitHubError("setting REPO is empty")
    if not REPO_NAME.fullmatch(repo):
        raise GitHubError("setting REPO is not owner/name")
    if not logins:
        raise GitHubError("setting GITHUB_LOGINS is empty")
    return Gh(repo, logins=logins)


def ticket_of(body: str):
    """The number in the first body line that reads ``Ticket: #<n>``, else None."""
    for line in body.splitlines():
        m = TICKET.match(line)
        if m:
            return int(m.group(1))
    return None


def _check_state(op: str, state: str, allowed: tuple) -> None:
    if state not in allowed:
        raise GitHubError(f"{op}: state {state!r} is not one of {', '.join(allowed)}")


def _now() -> str:
    return os.environ.get("FACTORY_NOW") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ended(body: str) -> str:
    return body if body.endswith("\n") else body + "\n"


def _sent(body: str, role: str) -> str:
    """The comment body ``Gh.post`` sends: the body, ended, and the role line."""
    return _ended(body) + f"<!-- factory:role {role} -->"


def fits(body: str, role: str) -> bool:
    """Whether the body posted as ``role`` is at most ``BODY_MAX`` characters once sent."""
    return len(_sent(body, role)) <= BODY_MAX


def _check_body(number, body, role) -> None:
    if not fits(body, role):
        raise GitHubError(f"post #{number}: the body is longer than {BODY_MAX} characters")


def _check_role(number, role) -> None:
    if not isinstance(role, str) or not ROLE.match(role):
        raise GitHubError(f"post #{number}: role must match {ROLE.pattern}")


def _check_labels(where: str, labels) -> None:
    if any(len(label) > LABEL_MAX for label in labels):
        raise GitHubError(f"{where}: a label is longer than {LABEL_MAX} characters")


class Recorded:
    """GitHub as a directory: ``issues/<n>.json``, ``pulls/<n>.json``, ``comments/<n>.json``, ``log.jsonl``.

    ``push`` and ``merge`` log their arguments without ``root``: a local path is not GitHub state.
    ``sync_base`` writes nothing and logs nothing: the recorded GitHub's base is the clone's own.
    Unlike ``Gh``, ``push`` and ``open_pull`` do not refuse a branch that does not exist.
    """

    def __init__(self, directory, logins=()):
        self.dir = Path(directory)
        self.logins = frozenset({FACTORY_LOGIN, *(name.lower() for name in logins)})

    # -- files

    def _load(self, rel: str):
        path = self.dir / rel
        return json.loads(path.read_text()) if path.is_file() else None

    def _all(self, kind: str) -> list:
        folder = self.dir / kind
        if not folder.is_dir():
            return []
        found = [json.loads(p.read_text()) for p in folder.glob("*.json") if p.stem.isdigit()]
        return sorted(found, key=lambda x: x["number"])

    def _write(self, rel: str, data) -> None:
        path = self.dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(data, indent=1, sort_keys=True) + "\n")
        os.replace(tmp, path)

    def _log(self, op: str, number, **arguments) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.dir / "log.jsonl", "a") as f:
            f.write(json.dumps({"op": op, "number": number, **arguments}, sort_keys=True) + "\n")

    def _next_number(self) -> int:
        numbers = [int(p.stem) for kind in ("issues", "pulls") if (self.dir / kind).is_dir()
                   for p in (self.dir / kind).glob("*.json") if p.stem.isdigit()]
        return max(numbers, default=0) + 1

    def _next_comment_id(self) -> int:
        folder = self.dir / "comments"
        ids = [c["id"] for p in folder.glob("*.json") for c in json.loads(p.read_text())] if folder.is_dir() else []
        return max(ids, default=0) + 1

    def _get(self, op: str, kind: str, number: int) -> dict:
        found = self._load(f"{kind}/{number}.json")
        if found is None:
            raise GitHubError(f"{op} #{number}: not found")
        return found

    def _issue_or_pull(self, op: str, number: int) -> tuple:
        for kind in ("issues", "pulls"):
            found = self._load(f"{kind}/{number}.json")
            if found is not None:
                return kind, found
        raise GitHubError(f"{op} #{number}: not found")

    # -- reads

    def issues(self, state="open") -> list:
        _check_state("issues", state, ISSUE_STATES)
        return [i for i in self._all("issues") if state == "all" or i["state"] == state]

    def pulls(self, state="open") -> list:
        _check_state("pulls", state, PULL_STATES)
        return [p for p in self._all("pulls") if state == "all" or p["state"] == state]

    def issue(self, number: int) -> dict:
        return self._get("issue", "issues", number)

    def pull(self, number: int) -> dict:
        return self._get("pull", "pulls", number)

    def _stored(self, number: int) -> list:
        return sorted(self._load(f"comments/{number}.json") or [], key=lambda c: (c["created_at"], c["id"]))

    def comments(self, number: int) -> list:
        self._issue_or_pull("comments", number)
        found = [{**c, "author": c.get("author", FACTORY_LOGIN)} for c in self._stored(number)]
        return [c for c in found if c["author"].lower() in self.logins]

    # -- writes

    def post(self, number: int, body: str, role: str) -> dict:
        _check_role(number, role)
        _check_body(number, body, role)
        self._issue_or_pull("post", number)
        comment = {"id": self._next_comment_id(), "created_at": _now(), "author": FACTORY_LOGIN, "role": role,
                   "body": _ended(body)}
        self._write(f"comments/{number}.json", self._stored(number) + [comment])
        self._log("post", number, body=body, role=role)
        return comment

    def create_issue(self, title: str, body: str, labels) -> int:
        labels = list(labels)
        _check_labels("create_issue", labels)
        number = self._next_number()
        self._write(f"issues/{number}.json", {
            "number": number, "title": title, "body": body, "state": "open",
            "labels": sorted(set(labels)), "created_at": _now()})
        self._log("create_issue", number, title=title, body=body, labels=labels)
        return number

    def close_issue(self, number: int) -> None:
        issue = self._get("close_issue", "issues", number)
        self._write(f"issues/{number}.json", {**issue, "state": "closed"})
        self._log("close_issue", number)

    def _relabel(self, op: str, number: int, label: str, add: bool) -> None:
        kind, item = self._issue_or_pull(op, number)
        labels = set(item["labels"]) | {label} if add else set(item["labels"]) - {label}
        self._write(f"{kind}/{number}.json", {**item, "labels": sorted(labels)})
        self._log(op, number, label=label)

    def add_label(self, number: int, label: str) -> None:
        _check_labels(f"add_label #{number}", [label])
        self._relabel("add_label", number, label, True)

    def remove_label(self, number: int, label: str) -> None:
        self._relabel("remove_label", number, label, False)

    def open_pull(self, head_ref: str, base_ref: str, title: str, body: str) -> int:
        for p in self.pulls("open"):
            if p["head_ref"] == head_ref:
                return p["number"]
        number = self._next_number()
        self._write(f"pulls/{number}.json", {
            "number": number, "title": title, "body": body, "state": "open", "labels": [],
            "head_ref": head_ref, "base_ref": base_ref, "ticket": ticket_of(body),
            "created_at": _now(), "merged_at": None})
        self._log("open_pull", number, head_ref=head_ref, base_ref=base_ref, title=title, body=body)
        return number

    def push(self, root, ref: str) -> None:
        self._log("push", None, ref=ref)

    def merge(self, root, number: int, head_sha: str) -> str:
        """Stand in for the server: merge ``head_ref`` into the checked-out base with a merge commit."""
        pull = self._get("merge", "pulls", number)

        def git(*args, env=None):
            return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                                  stdin=subprocess.DEVNULL, env=env)

        def refuse(reason, done=None):
            status = f" (exit {done.returncode})" if done is not None else ""
            return GitHubError(f"merge #{number}: {reason}{status}")

        if pull["state"] != "open":
            raise refuse(f"pull is {pull['state']}, not open")
        tip = git("rev-parse", "--verify", "-q", f"refs/heads/{pull['head_ref']}")
        if tip.returncode != 0 or tip.stdout.strip() != head_sha:
            raise refuse(f"branch tip of {pull['head_ref']} is not {head_sha}", tip)
        branch = git("symbolic-ref", "-q", "--short", "HEAD")
        if branch.returncode != 0 or branch.stdout.strip() != pull["base_ref"]:
            raise refuse(f"checked-out branch is not the base {pull['base_ref']}", branch)
        staged = git("diff", "--cached", "--quiet")
        if staged.returncode != 0:
            raise refuse("index is not clean", staged)
        env = dict(os.environ)
        if os.environ.get("FACTORY_NOW"):
            env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = os.environ["FACTORY_NOW"]
        merged = subprocess.run(
            ["git", *GIT_IDENTITY, "-C", str(root), "merge", "--no-ff", "--no-edit",
             "-m", f"Merge pull request #{number}", pull["head_ref"]],
            capture_output=True, text=True, stdin=subprocess.DEVNULL, env=env)
        if merged.returncode != 0:
            if git("rev-parse", "-q", "--verify", "MERGE_HEAD").returncode == 0:
                git("merge", "--abort")
            raise refuse(f"git merge of {pull['head_ref']} failed", merged)
        head = git("rev-parse", "HEAD").stdout.strip()
        self._write(f"pulls/{number}.json", {**pull, "state": "merged", "merged_at": _now()})
        self._log("merge", number, head_sha=head_sha)
        return head

    def sync_base(self, root, base_ref: str) -> str:
        """The tip of the local branch ``base_ref``; there is nothing to fetch."""
        done = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", "-q", f"refs/heads/{base_ref}"],
                              capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if done.returncode != 0:
            raise GitHubError(f"sync_base {base_ref}: git rev-parse failed (exit {done.returncode})")
        return done.stdout.strip()


def _issue(raw: dict) -> dict:
    return {"number": raw["number"], "title": raw["title"], "body": raw["body"],
            "state": raw["state"].lower(), "labels": sorted(label["name"] for label in raw["labels"]),
            "created_at": raw["createdAt"]}


def _rest_issue(raw: dict) -> dict:
    return _issue({**raw, "body": raw["body"] or "", "createdAt": raw["created_at"]})


def _pull(raw: dict) -> dict:
    return {**_issue(raw), "head_ref": raw["headRefName"], "base_ref": raw["baseRefName"],
            "ticket": ticket_of(raw["body"]), "merged_at": raw["mergedAt"]}


def _comment(raw: dict) -> dict:
    body, role = raw["body"], ""
    head, sep, last = body.rpartition("\n")
    m = ROLE_LINE.match(last)
    if m:
        body, role = head + sep, m.group(1)
    author = (raw.get("user") or {}).get("login") or ""
    return {"id": raw["id"], "created_at": raw["created_at"], "author": author, "role": role, "body": body}


def _answer(stdout: str) -> dict:
    try:
        found = json.loads(stdout)
    except ValueError:
        return {}
    return found if isinstance(found, dict) else {}


def _status(done):
    """The HTTP status of a failed call: the JSON answer's ``status``, else a stderr ending ``(HTTP <ddd>)``."""
    status = str(_answer(done.stdout).get("status", ""))
    if re.fullmatch(r"\d{3}", status):
        return status
    m = HTTP_STATUS.search((done.stderr or "").rstrip())
    return m.group(1) if m else None


def _failure(op: str, number, done) -> GitHubError:
    where = f" #{number}" if number is not None else ""
    status = _status(done)
    if status == "404" or (done.stderr or "").startswith(NOT_RESOLVED):
        return GitHubError(f"{op}{where}: not found")
    if status:
        return GitHubError(f"{op}{where}: exit {done.returncode} (HTTP {status})")
    return GitHubError(f"{op}{where}: exit {done.returncode}")


class Gh:
    """GitHub through ``gh`` and ``git``. ``run`` defaults to ``subprocess.run``, looked up on each call.

    A call that fails with a TLS handshake timeout sent nothing and is run again, ``RUNS`` runs at most.
    """

    def __init__(self, repo: str, run=None, logins=()):
        self.repo = repo
        self._run = run
        self.logins = frozenset(name.lower() for name in logins)

    def _start(self, argv: list, payload=None):
        run = self._run or subprocess.run
        stdin = "" if payload is None else json.dumps(payload)
        for _ in range(RUNS):
            done = run(argv, input=stdin, capture_output=True, text=True)
            if done.returncode == 0 or TLS_TIMEOUT not in (done.stderr or ""):
                break
        return done

    def _call(self, op: str, number, argv: list, payload=None) -> str:
        done = self._start(argv, payload)
        if done.returncode != 0:
            raise _failure(op, number, done)
        return done.stdout

    def _api(self, op: str, number, method: str, path: str, payload) -> dict:
        out = self._call(op, number, ["gh", "api", "-X", method, f"repos/{self.repo}/{path}", "--input", "-"], payload)
        return json.loads(out) if out.strip() else {}

    # -- reads

    def issues(self, state="open") -> list:
        """Every issue in ``state``, read page by page from the REST API, which also lists pulls."""
        _check_state("issues", state, ISSUE_STATES)
        out = self._call("issues", None, ["gh", "api", "--paginate", "--slurp",
                                          f"repos/{self.repo}/issues?state={state}&per_page=100"])
        found = [_rest_issue(raw) for page in json.loads(out) for raw in page if "pull_request" not in raw]
        return sorted((i for i in found if state == "all" or i["state"] == state), key=lambda i: i["number"])

    def pulls(self, state="open") -> list:
        _check_state("pulls", state, PULL_STATES)
        out = self._call("pulls", None, ["gh", "pr", "list", "-R", self.repo, "--state", state,
                                         "--limit", "1000", "--json", PULL_FIELDS])
        found = [_pull(raw) for raw in json.loads(out)]
        return sorted((p for p in found if state == "all" or p["state"] == state), key=lambda p: p["number"])

    def _view_issue(self, op: str, number: int) -> dict:
        """The issue ``number``; a pull's number is not found."""
        out = self._call(op, number, ["gh", "issue", "view", str(number), "-R", self.repo,
                                      "--json", ISSUE_VIEW_FIELDS])
        raw = json.loads(out)
        if not raw.get("url", "").endswith(f"/issues/{number}"):
            raise GitHubError(f"{op} #{number}: not found")
        return _issue(raw)

    def issue(self, number: int) -> dict:
        return self._view_issue("issue", number)

    def pull(self, number: int) -> dict:
        out = self._call("pull", number, ["gh", "pr", "view", str(number), "-R", self.repo,
                                          "--json", PULL_FIELDS])
        return _pull(json.loads(out))

    def comments(self, number: int) -> list:
        out = self._call("comments", number, ["gh", "api", "--paginate", "--slurp",
                                              f"repos/{self.repo}/issues/{number}/comments"])
        found = [_comment(raw) for page in json.loads(out) for raw in page]
        named = (c for c in found if c["author"].lower() in self.logins)
        return sorted(named, key=lambda c: (c["created_at"], c["id"]))

    # -- writes

    def post(self, number: int, body: str, role: str) -> dict:
        _check_role(number, role)
        _check_body(number, body, role)
        comment = _comment(self._api("post", number, "POST", f"issues/{number}/comments", {"body": _sent(body, role)}))
        if comment["author"].lower() not in self.logins:
            raise GitHubError(f"post #{number}: posted as a login that setting GITHUB_LOGINS does not name")
        return comment

    def create_issue(self, title: str, body: str, labels) -> int:
        labels = list(dict.fromkeys(labels))
        _check_labels("create_issue", labels)
        made = self._api("create_issue", None, "POST", "issues", {"title": title, "body": body, "labels": labels})
        return int(made["number"])

    def close_issue(self, number: int) -> None:
        self._view_issue("close_issue", number)
        self._api("close_issue", number, "PATCH", f"issues/{number}", {"state": "closed"})

    def add_label(self, number: int, label: str) -> None:
        _check_labels(f"add_label #{number}", [label])
        self._api("add_label", number, "POST", f"issues/{number}/labels", {"labels": [label]})

    def remove_label(self, number: int, label: str) -> None:
        """A label the issue does not carry is not an error; an issue that does not exist is."""
        path = f"repos/{self.repo}/issues/{number}/labels/{urllib.parse.quote(label, safe='')}"
        done = self._start(["gh", "api", "-X", "DELETE", path])
        if done.returncode == 0:
            return
        if _status(done) == "404" and _answer(done.stdout).get("message") == "Label does not exist":
            return
        raise _failure("remove_label", number, done)

    def open_pull(self, head_ref: str, base_ref: str, title: str, body: str) -> int:
        for p in self.pulls("open"):
            if p["head_ref"] == head_ref:
                return p["number"]
        made = self._api("open_pull", None, "POST", "pulls",
                         {"title": title, "body": body, "head": head_ref, "base": base_ref})
        return int(made["number"])

    def push(self, root, ref: str) -> None:
        self._call("push", None, ["git", "-C", str(root), "push", "origin", ref])

    def merge(self, root, number: int, head_sha: str) -> str:
        """Ask GitHub to merge an open pull at ``head_sha``; GitHub's merge sha. Starts no git: the local
        base moves with ``sync_base``. A ``GitHubError`` means GitHub did not merge."""
        state = self.pull(number)["state"]
        if state != "open":
            raise GitHubError(f"merge #{number}: pull is {state}, not open")
        done = self._api("merge", number, "PUT", f"pulls/{number}/merge", {"sha": head_sha, "merge_method": "merge"})
        return done["sha"]

    def sync_base(self, root, base_ref: str) -> str:
        """Fast-forward the local branch ``base_ref`` in ``root`` to GitHub's; its tip after the sync.

        A local base that is only ahead is left as it is (the gate pushes it again). One that has diverged
        is refused and nothing moves. A checked-out base moves with ``merge --ff-only``, keeping local
        changes the merge does not touch; any other with a fetch into the branch, ``HEAD`` untouched.
        """
        run = self._run or subprocess.run

        def git(*args) -> str:
            done = run(["git", "-C", str(root), *args], input="", capture_output=True, text=True)
            if done.returncode != 0:
                raise GitHubError(f"sync_base {base_ref}: git {args[0]} failed (exit {done.returncode})")
            return done.stdout.strip()

        local = f"refs/heads/{base_ref}"
        git("fetch", "origin", base_ref)
        ahead, behind = (int(n) for n in git("rev-list", "--left-right", "--count", f"{local}...FETCH_HEAD").split())
        if behind and ahead:
            raise GitHubError(f"sync_base {base_ref}: the local branch holds {ahead} commit(s) GitHub does not have")
        if behind:
            if git("rev-parse", "--abbrev-ref", "HEAD") == base_ref:
                git("merge", "--ff-only", "FETCH_HEAD")
            else:
                git("fetch", "origin", f"{base_ref}:{local}")
        return git("rev-parse", "--verify", local)
