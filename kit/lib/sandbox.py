"""The macOS sandbox a worker runs in: a `sandbox-exec` profile generated per launch (harness doc section 7).

The profile starts from `(allow default)` and denies what a worker may not do: writing outside its
worktree and scratch directory, reading secrets, the launcher's state directory or the credential
files under home, starting schedulers and services, and, for a role without a web tool, opening
network connections other than to its named endpoints.

The macOS sandbox cannot filter outbound connections by host name. An endpoint is `localhost:<port>`
or `*:<port>`, so a remote model endpoint is `*:443`; anything tighter needs a local egress proxy,
which this module does not provide. Only IP traffic is denied, so Unix-socket and local
name-resolution traffic that a process needs to resolve and connect stays allowed.

An error message names setting keys and fixed words, never a path.
"""

import os
import re

from kit.lib.frontmatter import as_list

SANDBOX_EXEC = "/usr/bin/sandbox-exec"
WEB_TOOLS = ("WebFetch", "WebSearch")
HOME_SECRETS = (".config/gh", ".gnupg", ".netrc", ".ssh", "Library/Keychains")
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


def profile(root, worktree, scratch, state_dir, secrets_dir, home, web=False, endpoints=()):
    """The SBPL profile for one launch. Equal arguments give a byte-identical profile."""
    allowed = [_endpoint(e) for e in endpoints]
    exec_paths = " ".join(f"(literal {_quote(d + '/' + n)})" for d in EXEC_DIRS for n in EXEC_DENIED)
    lines = [
        "(version 1)",
        "(allow default)",
        f"(deny file-read* {_subpaths(state_dir)})",
        f"(allow file-read* {_subpaths(worktree, scratch)})",
        f"(deny file-read* {_subpaths(secrets_dir)})",
        f"(deny file-read* {_subpaths(*(os.path.join(home, p) for p in HOME_SECRETS))})",
        "(deny file-write*)",
        f'(allow file-write* {_subpaths(worktree, scratch)} (literal "/dev/null") (literal "/dev/tty"))',
        f"(deny file-write* {_subpaths(*(os.path.join(worktree, p) for p in WORKTREE_PROTECTED))})",
        f"(deny process-exec {exec_paths})",
    ]
    if not web:
        lines.append('(deny network-outbound (remote ip "*:*"))')
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
