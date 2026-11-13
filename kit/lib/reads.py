"""factory run --read <topic> <platform> [--root DIR] [--apply]: one read of other people's videos (brief 16).

A read is not a gated job: it needs no job file, no PASS and no pull, connects to no GitHub and writes no
ledger row. Its tool is the file `kit/lib/tools/reads/<platform>.py` (`kit/lib/tools/__init__.py` states its
interface). In this order: the topic and the platform, the tool, `STATE_DIR`; then the stored reads of every
topic on the platform 30 days old or more are deleted; then the platform's part of
`research/topics/<topic>.json` on the base, the tool's resource, the API URL (setting
`<PLATFORM>_API_URL`, else the tool's `API`) and the day's budget of searches, a count in
`<STATE_DIR>/read-budget/<platform>.json` that the read spends before its call and never gives back.
The read is stored as `<STATE_DIR>/reads/<topic>/<platform>/<stamp>.json` (`kit/lib/trends.py`).

Without `--apply` nothing is deleted, counted or called: each line says what would be done. Exit 0 stored
or would read, 1 the read failed, 2 refused or the resource is down. Nothing a tool prints, raises or
returns other than its videos and a reason out of its own `REASONS` is repeated anywhere.
"""

import importlib.util
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from kit.lib import cmd_run, gitstate, media, production, resources, trends
from kit.lib.tools import Down, Failed

TOOLS_DIR = Path(__file__).resolve().parent / "tools" / "reads"
EXPIRY_S = 30 * 24 * 3600
BUDGET_DIR = "read-budget"
WATCH_KEYS = ["days", "region", "words"]
REGION = re.compile(r"[A-Z]{2}")
WHOLE = re.compile(r"[0-9]+")
Refused = cmd_run.Refused


def load_tool(platform):
    """The module of the read tool ``platform``, or None when its file cannot be run."""
    try:
        with cmd_run._silenced():
            spec = importlib.util.spec_from_file_location(f"factory_read_{platform}", TOOLS_DIR / f"{platform}.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    except (Exception, SystemExit):
        return None
    return module


def _follows(tool, platform):
    budget = getattr(tool, "BUDGET", None)
    reasons = getattr(tool, "REASONS", None)
    return (getattr(tool, "PLATFORM", None) == platform
            and isinstance(getattr(tool, "RESOURCE", None), str) and tool.RESOURCE != ""
            and isinstance(reasons, tuple) and all(isinstance(r, str) for r in reasons)
            and isinstance(getattr(tool, "API", None), str)
            and isinstance(budget, tuple) and len(budget) == 2 and isinstance(budget[0], str)
            and isinstance(budget[1], int) and not isinstance(budget[1], bool) and budget[1] >= 0
            and callable(getattr(tool, "read", None)))


def _tool(platform):
    if not (TOOLS_DIR / f"{platform}.py").is_file():
        raise Refused(f"no read tool for {platform}")
    tool = load_tool(platform)
    if not _follows(tool, platform):
        raise Refused(f"read tool {platform} does not follow the read interface")
    return tool


def _time(stamp):
    return datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def _expire(state, platform, now, apply):
    """Delete (with ``apply``) every read on ``platform`` 30 days old or more, for every topic."""
    reads = state / "reads"
    topics = sorted(e.name for e in os.scandir(reads) if e.is_dir()) if reads.is_dir() else []
    verb = "deleted" if apply else "would delete"
    for topic in topics:
        directory = reads / topic / platform
        if not directory.is_dir():
            continue
        for name in sorted(e.name for e in os.scandir(directory) if e.is_file(follow_symlinks=False)):
            if not trends._READ_NAME.fullmatch(name):
                continue
            try:
                age = (now - _time(name[:-len(".json")])).total_seconds()
            except ValueError:
                continue
            if age >= EXPIRY_S:
                if apply:
                    os.remove(directory / name)
                print(f"{verb} reads/{topic}/{platform}/{name}: older than 30 days")


def _whole(value, low, high=None):
    return (isinstance(value, int) and not isinstance(value, bool) and low <= value
            and (high is None or value <= high))


def _watch(p, topic, platform):
    """The platform's part of the topic file on the base: ``{words, region, days}``."""
    rel, base = trends.topic_file(topic), production.base(p)
    text = gitstate.show(p.root, base, rel)
    if text is None:
        raise Refused(f"{rel} is not on {base}")
    try:
        doc = json.loads(text)
    except ValueError:
        doc = None
    if not isinstance(doc, dict) or platform not in doc:
        raise Refused(f"{rel} does not watch {platform}")
    watch = doc[platform]
    if not (isinstance(watch, dict) and sorted(watch) == WATCH_KEYS
            and isinstance(watch["words"], list) and watch["words"]
            and all(isinstance(w, str) and w for w in watch["words"])
            and isinstance(watch["region"], str) and REGION.fullmatch(watch["region"])
            and _whole(watch["days"], 1, 365)):
        raise Refused(f"{rel}: {platform} is not {{words, region, days}}")
    return watch


def _api(p, tool, platform):
    name = f"{platform.upper()}_API_URL"
    found = media.base(p.settings.get(name) or tool.API)
    if found is None:
        raise Refused(f"setting {name} is not an API URL")
    return found


def _limit(p, tool):
    name, most = tool.BUDGET
    value = production.setting(p, name, str(most))
    if not WHOLE.fullmatch(value) or int(value) > most:
        raise Refused(f"setting {name} must be a whole number from 0 to {most}")
    return int(value)


def _spent(path, platform, day):
    """The searches spent on ``day``, from the budget file; 0 without one or for another day."""
    if not path.exists():
        return 0
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError):
        doc = None
    if not (isinstance(doc, dict) and sorted(doc) == ["day", "searches"] and isinstance(doc["day"], str)
            and re.fullmatch(r"\d{4}-\d{2}-\d{2}", doc["day"]) and _whole(doc["searches"], 0)):
        raise Refused(f"{BUDGET_DIR}/{platform}.json is not a budget file")
    return doc["searches"] if doc["day"] == day else 0


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _call(tool, call, doc, rel, name, topic, platform):
    """``(reason, videos, text)``: the reason the read failed, or its videos and the text to store.
    ``Down`` passes through."""
    try:
        with cmd_run._silenced():
            returned = tool.read(call)
    except Down:
        raise
    except Failed as failure:
        return (failure.reason if failure.reason in tool.REASONS else cmd_run.TOOL_FAILED), None, None
    except (Exception, SystemExit):
        return cmd_run.CRASHED, None, None
    if not (isinstance(returned, dict) and list(returned) == ["videos"] and isinstance(returned["videos"], list)):
        return cmd_run.BROKE, None, None
    doc = {**doc, "videos": returned["videos"]}
    try:
        trends._check_read(rel, name, doc, topic, platform)
        return None, doc["videos"], json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    except (trends.TrendError, TypeError, ValueError):
        return cmd_run.BROKE, None, None


def main(p, topic, platform, apply):
    """One read of ``topic`` on ``platform``; the exit code. ``Refused`` for a refusal."""
    if not trends.TOPIC.fullmatch(topic):
        raise Refused(f"topic must match {trends.TOPIC.pattern}")
    if platform not in trends.PLATFORMS:
        raise Refused(f"platform must be one of {', '.join(trends.PLATFORMS)}")
    tool = _tool(platform)
    state = production.state_dir(p)
    read_at = production.now()
    stamp = read_at.replace("-", "").replace(":", "")
    _expire(state, platform, _time(stamp), apply)
    watch = _watch(p, topic, platform)
    resource, values, register = cmd_run._resource(platform, tool, p.root)
    api = _api(p, tool, platform)
    limit = _limit(p, tool)
    day = read_at[:10]
    budget = state / BUDGET_DIR / f"{platform}.json"
    spent = _spent(budget, platform, day)
    if spent >= limit:
        raise Refused(f"read {topic} {platform}: {spent} of {limit} searches of {day} are spent")
    name = stamp + ".json"
    rel = f"reads/{topic}/{platform}/{name}"
    path = trends.reads_dir(p, topic, platform) / name
    if os.path.lexists(path):
        raise Refused(f"{rel} is already stored")
    if not apply:
        print(f"read {topic} {platform}: would search, {spent} of {limit} searches spent on {day}")
        return 0
    spent += 1
    _write(budget, json.dumps({"day": day, "searches": spent}, sort_keys=True) + "\n")
    secrets_dir = resources.expand(values[register["secrets_dir_setting"]])
    call = {
        "api": api, "read_at": read_at, "topic": watch,
        "secrets": {c["name"]: os.path.abspath(os.path.join(secrets_dir, c["name"]))
                    for c in resource["checks"] if c["kind"] == "secret"},
    }
    doc = {"platform": platform, "read_at": read_at, "topic": topic}
    try:
        reason, videos, text = _call(tool, call, doc, rel, name, topic, platform)
    except Down:
        raise Refused(f"resource {resource['id']} is down") from None
    if reason is not None:
        print(f"run: read {topic} {platform} failed: {reason}", file=sys.stderr)
        return 1
    _write(path, text)
    print(f"read {topic} {platform} at {read_at}: {len(videos)} videos, "
          f"search {spent} of {limit} on {day}")
    print(f"wrote {rel}")
    return 0
