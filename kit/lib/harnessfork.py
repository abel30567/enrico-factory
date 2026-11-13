"""The producer's check of a harness-fork issue (docs/harness.md section 9, level 4).

A fork issue is an issue that is not a ticket and carries the label ``harness-fork``. Its body has a head
line ``role: <role id>`` and four sections, ``## Lab programs``, ``## Cause``, ``## Reproduction`` and
``## Pinned build``. The check reads the role's eval suite in the working tree, the lab's loops and
``lab/results.tsv`` on the base, and the pinned build's version through ``harness.version``: it never runs
the reproduction or a model. ``decide`` gives ``(verdict, reason)``; the first condition that fails is the
reason, and nothing after it is checked.
"""

from __future__ import annotations

import hashlib
import re

from kit.lib import gitstate, harness, lab, production

LABEL = "harness-fork"
HOLD = "the three conditions hold"
PROGRAMS, CAUSE, REPRODUCTION, PINNED = "Lab programs", "Cause", "Reproduction", "Pinned build"
PINNED_KEYS = ("harness", "command", "exit", "settings tried")

_PROGRAM = re.compile(r"- (\S+) lines (\d+)-(\d+)")
_EXIT = re.compile(r"-?[0-9]+")


class Stop(Exception):
    pass


def issue(state, number):
    """The issue ``number`` when it is a fork issue, in any state; else None."""
    return next((i for i in state.others if i["number"] == number and LABEL in i["labels"]), None)


def digest(number, text):
    """The hash of the decision on fork issue ``number`` with body ``text``."""
    return hashlib.sha256(f"fork\n{number}\n{text}".encode("utf-8")).hexdigest()


def body(hash_, verdict, reason):
    """The gate's comment on a fork issue: no cap and no ledger line."""
    from kit.lib import cmd_gate  # cmd_gate imports this module
    return "\n".join([f"[PRODUCER] GATE {verdict}", f"covers: fork {hash_}", cmd_gate._cap_line("-", reason),
                      "ledger: -"]) + "\n"


def parse(text):
    """``(head lines, {section name: [lists of lines, one per time it appears]})``."""
    head, sections, current = [], {}, None
    for line in (raw.rstrip() for raw in text.split("\n")):
        if line.startswith("## "):
            current = []
            sections.setdefault(line[3:], []).append(current)
        elif current is None:
            head.append(line)
        else:
            current.append(line)
    return head, sections


def value(lines, key):
    """The value of the first line ``<key>: <value>`` with a value, stripped; else None."""
    for line in lines:
        if line.startswith(key + ":") and line[len(key) + 1:].strip():
            return line[len(key) + 1:].strip()
    return None


def _section(sections, name, condition):
    found = sections.get(name, [])
    if len(found) > 1:
        raise Stop(f"condition {condition}: section ## {name} appears {len(found)} times")
    if not found:
        raise Stop(f"condition {condition}: no section ## {name}")
    return found[0]


def _program(p, base, rows, role, line):
    """``(loop, first, last)`` of one line of ``## Lab programs``; ``Stop`` when it does not hold."""
    m = _PROGRAM.fullmatch(line)
    if not m:
        raise Stop(f"condition 1: line '{line}' is not - <loop> lines <first>-<last>")
    loop, first, last = m.group(1), int(m.group(2)), int(m.group(3))
    try:
        spec = lab.load_loop(p.root, base, loop)
    except lab.LabError as error:
        raise Stop(f"condition 1: {error}") from None
    if role not in spec["command"][1:]:
        raise Stop(f"condition 1: loop {loop} does not run the eval of {role}")
    if not 2 <= first <= last <= len(rows) + 1:
        raise Stop(f"condition 1: {loop} lines {first}-{last} are not rows of lab/results.tsv")
    for n in (first, last):
        if rows[n - 2]["loop"] != loop:
            raise Stop(f"condition 1: line {n} of lab/results.tsv is not a row of loop {loop}")
    if not any(r["loop"] == loop and r["kind"] == "run" for r in rows[first - 2:last - 1]):
        raise Stop(f"condition 1: {loop} lines {first}-{last} hold no run row")
    return loop, first, last


def _condition_1(p, sections, role):
    lines = [line.strip() for line in _section(sections, PROGRAMS, 1) if line.strip()]
    if len(lines) != 3:
        raise Stop(f"condition 1: ## {PROGRAMS} names {len(lines)} programs, not 3")
    base = production.base(p)
    text = gitstate.show(p.root, base, lab.PATH)
    if text is None:
        raise Stop(f"condition 1: no {lab.PATH} on {base}")
    try:
        rows = lab.read(text)
    except lab.LabError as error:
        raise Stop(f"condition 1: {error}") from None
    programs = [_program(p, base, rows, role, line) for line in lines]
    if any(later[1] <= earlier[2] for earlier, later in zip(programs, programs[1:])):
        raise Stop("condition 1: the three programs are not in order without overlap")
    loops = {loop for loop, _, _ in programs}
    for n in range(programs[0][1], len(rows) + 2):
        if rows[n - 2]["loop"] in loops and rows[n - 2]["verdict"] == "KEEP":
            raise Stop(f"condition 1: line {n} of lab/results.tsv is a KEEP of loop {rows[n - 2]['loop']}")


def _condition_2(sections, role):
    if not any(line.strip() for line in _section(sections, CAUSE, 2)):
        raise Stop(f"condition 2: section ## {CAUSE} is empty")
    command = value(_section(sections, REPRODUCTION, 2), "command")
    if command is None:
        raise Stop(f"condition 2: ## {REPRODUCTION} has no line command: <command>")
    if "roles/" in command or role in command.split():
        raise Stop(f"condition 2: the reproduction runs the prompt of {role}")
    return command


def _condition_3(p, sections, command):
    lines = _section(sections, PINNED, 3)
    values = {key: value(lines, key) for key in PINNED_KEYS}
    missing = next((key for key in PINNED_KEYS if values[key] is None), None)
    if missing is not None:
        raise Stop(f"condition 3: ## {PINNED} has no line {missing}: <value>")
    try:
        pinned = harness.version(p.settings).strip()
    except harness.HarnessError:
        raise Stop("condition 3: the pinned build's version cannot be read") from None
    if values["harness"] != pinned:
        raise Stop(f"condition 3: harness {values['harness']} is not the pinned build {pinned}")
    if values["command"] != command:
        raise Stop("condition 3: the command is not the reproduction's")
    if not _EXIT.fullmatch(values["exit"]) or int(values["exit"]) == 0:
        raise Stop(f"condition 3: exit {values['exit']} is not a failing exit status")


def _check(p, text):
    head, sections = parse(text)
    role = value(head, "role")
    if role is None:
        raise Stop("the issue has no line role: <role id>")
    if role not in p.roles:
        raise Stop(f"role {role} is not a role of the kit")
    if not (p.kit / "evals" / role / "suite.json").is_file():
        raise Stop(f"role {role} has no eval suite evals/{role}/")
    _condition_1(p, sections, role)
    command = _condition_2(sections, role)
    _condition_3(p, sections, command)


def decide(p, found):
    """``("PASS", HOLD)`` when the fork issue ``found`` meets the three conditions, else ``("STOP", reason)``."""
    try:
        _check(p, found["body"])
    except Stop as stop:
        return "STOP", str(stop)
    return "PASS", HOLD
