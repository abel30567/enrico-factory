"""The resources register: load and validate it, read a production's settings, check one resource.

A reason never contains a value: it is built only from text in the register and fixed words, never
from a setting's value, a path taken from a setting, a secret's content or a command's output.
"""

import importlib.util
import json
import os
import re
import stat
import subprocess
from pathlib import Path

REGISTER = Path(__file__).resolve().parent.parent / "resources.json"

AUTH = ("none", "login", "key", "grant")
HOLDER = ("none", "launcher", "gateway", "run")
SETUP = ("owner", "agent")
ROOMS = ("story", "production", "post", "distribution", "lab", "all")
REQUIRED = ("id", "title", "class", "auth", "holder", "rooms", "wave", "setup", "checks")
OPTIONAL = ("optional", "renew")
CHECK_FIELDS = {
    "command": (("argv",), ("match_all",)),
    "module": (("name",), ()),
    "secret": (("name",), ("min",)),
    "setting": (("name",), ("one_of",)),
    "path": (("setting",), ()),
}
TIMEOUT = 20
PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_text(value):
    return isinstance(value, str) and value != ""


def _check_problems(check):
    if not isinstance(check, dict):
        return ["check is not an object"]
    kind = check.get("kind")
    required, optional = CHECK_FIELDS[kind]
    problems = []
    for key in sorted(set(check) - {"kind"} - set(required) - set(optional)):
        problems.append(f"{kind} check has unknown key {key}")
    for key in required:
        if key not in check:
            problems.append(f"{kind} check has no {key}")
    if kind == "command":
        argv = check.get("argv")
        if "argv" in check and not (isinstance(argv, list) and argv and all(isinstance(a, str) for a in argv)):
            problems.append("command check argv is not a non-empty list of strings")
        if "match_all" in check:
            patterns = check["match_all"]
            if not (isinstance(patterns, list) and all(isinstance(p, str) for p in patterns)):
                problems.append("command check match_all is not a list of strings")
            else:
                for pattern in patterns:
                    try:
                        re.compile(pattern)
                    except re.error:
                        problems.append(f"command check pattern does not compile: {pattern}")
    for key in ("name", "setting"):
        if key in required and key in check and not _is_text(check[key]):
            problems.append(f"{kind} check {key} is not a non-empty string")
    if kind == "secret" and "min" in check and not (_is_int(check["min"]) and check["min"] >= 1):
        problems.append("secret check min is not an integer of at least 1")
    if kind == "setting" and "one_of" in check:
        values = check["one_of"]
        if not (isinstance(values, list) and all(isinstance(v, str) for v in values)):
            problems.append("setting check one_of is not a list of strings")
    return problems


def _resource_problems(resource, kinds):
    if not isinstance(resource, dict):
        return ["resource is not an object"]
    problems = []
    for key in REQUIRED:
        if key not in resource:
            problems.append(f"missing {key}")
    for key in sorted(set(resource) - set(REQUIRED) - set(OPTIONAL)):
        problems.append(f"unknown key {key}")
    for key in ("title", "class"):
        if key in resource and not _is_text(resource[key]):
            problems.append(f"{key} is not a non-empty string")
    for key, allowed in (("auth", AUTH), ("holder", HOLDER), ("setup", SETUP)):
        if key in resource and resource[key] not in allowed:
            problems.append(f"{key} is not one of {', '.join(allowed)}")
    if "rooms" in resource:
        rooms = resource["rooms"]
        if not (isinstance(rooms, list) and rooms):
            problems.append("rooms is not a non-empty list")
        else:
            for room in rooms:
                if room not in ROOMS:
                    problems.append(f"rooms has an unknown room: {room}")
    if "wave" in resource and not (_is_int(resource["wave"]) and 1 <= resource["wave"] <= 7):
        problems.append("wave is not an integer from 1 to 7")
    if "optional" in resource and not isinstance(resource["optional"], bool):
        problems.append("optional is not true or false")
    if "checks" in resource:
        checks = resource["checks"]
        if not (isinstance(checks, list) and checks):
            problems.append("checks is not a non-empty list")
        else:
            for number, check in enumerate(checks, 1):
                kind = check.get("kind") if isinstance(check, dict) else None
                if not isinstance(kind, str) or kind not in kinds:
                    problems.append(f"check {number}: kind is not a key of check_kinds")
                elif kind not in CHECK_FIELDS:
                    problems.append(f"check {number}: kind {kind} is not supported")
                else:
                    problems.extend(f"check {number}: {p}" for p in _check_problems(check))
    return problems


def validate(register):
    """Return the register's problems, each naming a resource id (or `register`), sorted."""
    if not isinstance(register, dict):
        return ["register: not a JSON object"]
    problems = []
    if not (_is_int(register.get("version")) and register["version"] == 1):
        problems.append("register: version is not 1")
    files = register.get("settings_files")
    if not (isinstance(files, list) and all(_is_text(f) for f in files)):
        problems.append("register: settings_files is not a list of file names")
    if not _is_text(register.get("secrets_dir_setting")):
        problems.append("register: secrets_dir_setting is not a setting name")
    kinds = register.get("check_kinds")
    if not isinstance(kinds, dict):
        problems.append("register: check_kinds is not an object")
        kinds = {}
    resources = register.get("resources")
    if not isinstance(resources, list):
        return sorted(problems + ["register: resources is not a list"])
    seen = set()
    for index, resource in enumerate(resources):
        rid = resource.get("id") if isinstance(resource, dict) else None
        if _is_text(rid):
            name = rid
            if rid in seen:
                problems.append(f"{rid}: id is not unique")
            seen.add(rid)
        else:
            name = f"resources[{index}]"
            if isinstance(resource, dict) and "id" in resource:
                problems.append(f"{name}: id is not a non-empty string")
        problems.extend(f"{name}: {p}" for p in _resource_problems(resource, kinds))
    return sorted(problems)


def load(path):
    """Read and validate the register at `path`. Returns (register, problems)."""
    try:
        with open(path, encoding="utf-8") as handle:
            register = json.load(handle)
    except OSError:
        return None, ["register: cannot be read"]
    except ValueError:
        return None, ["register: not valid JSON"]
    return register, validate(register)


def _shipped():
    register, problems = load(REGISTER)
    if problems:
        raise ValueError("the shipped register has problems: " + "; ".join(problems))
    return register


def settings(root, register=None):
    """Read the register's settings files from `root`, in order; a later file wins."""
    register = register or _shipped()
    values = {}
    for name in register["settings_files"]:
        try:
            with open(os.path.join(root, name), encoding="utf-8") as handle:
                lines = handle.read().splitlines()
        except FileNotFoundError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            values[key] = value
    return values


def _expand(value):
    return os.path.expanduser(value) if value.startswith("~/") else value


def _empty(name):
    return f"setting {name} is empty"


def _check_command(check, values):
    shown = " ".join(check["argv"])
    argv = []
    for arg in check["argv"]:
        names = PLACEHOLDER.findall(arg)
        empty = [n for n in names if not values.get(n)]
        if empty:
            return [_empty(n) for n in empty]
        argv.append(PLACEHOLDER.sub(lambda m: values[m.group(1)], arg))
    try:
        done = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=TIMEOUT)
    except FileNotFoundError:
        return [f"command not found: {shown}"]
    except subprocess.TimeoutExpired:
        return [f"command timed out: {shown}"]
    except OSError:
        return [f"command could not start: {shown}"]
    if done.returncode != 0:
        return [f"command failed: {shown}"]
    output = (done.stdout + done.stderr).decode("utf-8", "replace")
    return [f"pattern not found: {p}" for p in check.get("match_all", []) if not re.search(p, output)]


def _usable(path):
    """None when `path` is a non-empty regular file open to its owner only, otherwise the defect."""
    info = os.stat(path)
    if not stat.S_ISREG(info.st_mode):
        return "not a file"
    if info.st_size == 0:
        return "empty"
    if info.st_mode & 0o077:
        return "readable by others" if info.st_mode & 0o044 else "open to group or others"
    return None


def _check_secret(check, values, dir_setting):
    name = check["name"]
    base = values.get(dir_setting)
    if not base:
        return [_empty(dir_setting)]
    path = os.path.join(_expand(base), name)
    try:
        if os.path.isdir(path):
            least = check.get("min", 1)
            usable = 0
            for entry in sorted(os.listdir(path)):
                try:
                    usable += _usable(os.path.join(path, entry)) is None
                except OSError:
                    pass
            if usable < least:
                return [f"secret {name}: fewer than {least} usable files"]
            return []
        defect = _usable(path)
    except FileNotFoundError:
        return [f"secret {name}: missing"]
    except OSError:
        return [f"secret {name}: cannot be read"]
    return [f"secret {name}: {defect}"] if defect else []


def _check_module(check):
    try:
        found = importlib.util.find_spec(check["name"]) is not None
    except (ImportError, ValueError):
        found = False
    return [] if found else [f"module not found: {check['name']}"]


def _check_setting(check, values):
    name = check["name"]
    value = values.get(name)
    if not value:
        return [_empty(name)]
    if "one_of" in check and value not in check["one_of"]:
        return [f"setting {name} is not one of: {', '.join(check['one_of'])}"]
    return []


def _check_path(check, values):
    name = check["setting"]
    value = values.get(name)
    if not value:
        return [_empty(name)]
    if not os.path.exists(_expand(value)):
        return [f"setting {name} names nothing that exists"]
    return []


def check(resource, settings, register=None):
    """Run every check of `resource` against `settings`. Returns (passed, reasons)."""
    register = register or _shipped()
    reasons = []
    for item in resource["checks"]:
        kind = item["kind"]
        if kind == "command":
            reasons += _check_command(item, settings)
        elif kind == "module":
            reasons += _check_module(item)
        elif kind == "secret":
            reasons += _check_secret(item, settings, register["secrets_dir_setting"])
        elif kind == "setting":
            reasons += _check_setting(item, settings)
        elif kind == "path":
            reasons += _check_path(item, settings)
    return not reasons, reasons
