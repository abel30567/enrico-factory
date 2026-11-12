"""The macOS sandbox a worker runs in: a `sandbox-exec` profile generated per launch (harness doc section 7).

The profile starts from `(allow default)` and denies what a worker may not do: writing outside its
worktree and scratch directory, reading secrets, the launcher's state directory, the credential
stores under home (`HOME_SECRETS`, among them the harness's own default configuration) or the paths
of setting `SANDBOX_DENY`, starting schedulers and services, and, for a role without a web tool,
opening network connections other than to its named endpoints.

A read deny names a directory's content, its listing and its `stat` alike. Every path is resolved
with `os.path.realpath`, so a symbolic link denies what it points to. A `SANDBOX_DENY` entry must be
absolute and must not be or contain the production root, the worktree or the scratch directory.

The kernel checks a started program by its resolved path. So the exec deny names each program of
`EXEC_DENIED` in every directory of `EXEC_DIRS`, in every absolute directory of the worker's `PATH`
where it is found, and the resolved path of every place where it exists. A copy of a denied program
made by the worker still runs. The profile therefore depends on the file system: equal arguments on
an unchanged file system give a byte-identical profile.

The macOS sandbox cannot filter outbound connections by host name. An endpoint is `localhost:<port>`
or `*:<port>`, so a remote model endpoint is `*:443`; anything tighter needs a local egress proxy,
which this module does not provide. Unix-socket traffic, which local name resolution needs, stays
allowed. The deny rule has no filter: on macOS 14 a deny filtered by `(remote ip "*:*")` also denies
a fixed set of allowed ports, roughly one in seven.

An error message names setting keys and fixed words, never a path.
"""

import os
import re

from kit.lib.frontmatter import as_list

SANDBOX_EXEC = "/usr/bin/sandbox-exec"
WEB_TOOLS = ("WebFetch", "WebSearch")
HOME_SECRETS = (".claude", ".claude.json", ".config/gh", ".gnupg", ".netrc", ".ssh", "Library/Keychains")
WORKTREE_PROTECTED = (".factory", ".git")
EXEC_DIRS = ("/usr/bin", "/bin", "/usr/local/bin", "/opt/homebrew/bin")
EXEC_DENIED = ("launchctl", "osascript", "crontab", "at", "gh")
_ENDPOINT = re.compile(r"(localhost|\*):([0-9]{1,5})")


class SandboxError(Exception):
    pass


def _quote(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _subpaths(*paths):
    return " ".join(f"(subpath {_quote(os.path.realpath(p))})" for p in paths)


def _endpoint(endpoint):
    match = _ENDPOINT.fullmatch(endpoint) if isinstance(endpoint, str) else None
    if not match or not 1 <= int(match.group(2)) <= 65535:
        raise SandboxError("endpoint must be localhost:<port> or *:<port>")
    return endpoint


def _contains(outer, inner):
    return os.path.commonpath([outer, inner]) == outer


def _denied(entries, root, worktree, scratch):
    """The resolved `SANDBOX_DENY` entries, sorted, each once; `SandboxError` for one that cannot be used."""
    resolved = set()
    kept = [os.path.realpath(p) for p in (root, worktree, scratch)]
    for entry in entries:
        if not isinstance(entry, str) or not os.path.isabs(entry):
            raise SandboxError("setting SANDBOX_DENY holds a path that is not absolute")
        real = os.path.realpath(entry)
        if any(_contains(real, path) for path in kept):
            raise SandboxError("setting SANDBOX_DENY holds a path that contains the production, "
                               "the worktree or the run directory")
        resolved.add(real)
    return sorted(resolved)


def _exec_denied(path):
    """Every path a program of `EXEC_DENIED` is started by: in `EXEC_DIRS`, in `path`, and resolved."""
    dirs = [d for d in path.split(os.pathsep) if os.path.isabs(d)]
    places = {os.path.join(d, n) for d in EXEC_DIRS for n in EXEC_DENIED}
    places |= {os.path.join(d, n) for d in dirs for n in EXEC_DENIED if os.path.exists(os.path.join(d, n))}
    places |= {os.path.realpath(p) for p in places if os.path.exists(p)}
    return sorted(places)


def profile(root, worktree, scratch, state_dir, secrets_dir, home, web=False, endpoints=(), deny=(), path=""):
    """The SBPL profile for one launch. Equal arguments on an unchanged file system give a byte-identical profile.

    `deny` adds paths to the rule of `HOME_SECRETS`; `path` is the worker's `PATH`.
    """
    denied = _denied(deny, root, worktree, scratch)
    allowed = [_endpoint(e) for e in endpoints]
    exec_paths = " ".join(f"(literal {_quote(p)})" for p in _exec_denied(path))
    lines = [
        "(version 1)",
        "(allow default)",
        f"(deny file-read* {_subpaths(state_dir)})",
        f"(allow file-read* {_subpaths(worktree, scratch)})",
        f"(deny file-read* {_subpaths(secrets_dir)})",
        f"(deny file-read* {_subpaths(*(os.path.join(home, p) for p in HOME_SECRETS), *denied)})",
        "(deny file-write*)",
        f'(allow file-write* {_subpaths(worktree, scratch)} (literal "/dev/null") (literal "/dev/tty"))',
        f"(deny file-write* {_subpaths(*(os.path.join(worktree, p) for p in WORKTREE_PROTECTED))})",
        f"(deny process-exec {exec_paths})",
    ]
    if not web:
        lines += ["(deny network-outbound)", "(allow network-outbound (remote unix-socket))"]
        lines += [f"(allow network-outbound (remote ip {_quote(e)}))" for e in sorted(set(allowed))]
    return "\n".join(lines) + "\n"


def web(meta):
    """True when the role's tools name a web tool."""
    return any(tool in WEB_TOOLS for tool in as_list(meta.get("tools")))


def wrap(argv, profile_path):
    return [SANDBOX_EXEC, "-f", str(profile_path), *argv]


def available():
    return os.path.isfile(SANDBOX_EXEC) and os.access(SANDBOX_EXEC, os.X_OK)


def write(path, text):
    """Write the profile readable and writable by the owner only."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)


def apply(settings, argv, profile_path):
    """`argv` wrapped in the sandbox when setting SANDBOX is on (the default), unchanged when off."""
    value = settings.get("SANDBOX", "on")
    if value == "off":
        return list(argv)
    if value != "on":
        raise SandboxError("setting SANDBOX is not on or off")
    if not available():
        raise SandboxError("sandbox is on and sandbox-exec is not available")
    return wrap(argv, profile_path)
