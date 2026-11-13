"""Judge calibration: the rank agreement of a tuned judge with real retention on held-out episodes (brief section 13).

The held-out list is `lab/held-out/<loop>.json` under the root: `{episodes, judge, platform}`. Episode `<ep>`'s
post is `<ep>/<platform>`; its real retention is the last beat's of `calibration.rows` under the root, never the
tree measured. In the tree measured, the tuned judge must not read a held-out episode's retention: no held-out
row in `calibration/retention.tsv`, and no `reads` of `.factory/roles/<judge>.md` matching its `retention.json`.
The verdicts are `lab/verdicts/<loop>.tsv` in the tree measured, one preference of the judge per pair; the
agreement is the share of counted verdicts that prefer the episode with the higher retention.
"""

from __future__ import annotations

import decimal
import json
import re
from pathlib import Path

from kit.lib import calibration, frontmatter, production

PLACES = 4
KEYS = ("episodes", "judge", "platform")
VERDICT_HEADER = ("judge", "a", "b", "marker")
MARKERS = ("PREFERS A", "PREFERS B", "NO PREFERENCE")

_LOOP_ID = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
_EPISODE = re.compile(r"[^/\s]+")
_JUDGE = re.compile(r"[a-z][a-z0-9]*(-[a-z0-9]+)*")
_PLATFORM = re.compile(r"[a-z0-9]+")


class AgreementError(Exception):
    pass


def check_loop(loop: str) -> None:
    if not _LOOP_ID.fullmatch(loop):
        raise AgreementError(f"'{loop}' is not a loop id")


def held_out_path(loop: str) -> str:
    return f"lab/held-out/{loop}.json"


def verdicts_path(loop: str) -> str:
    return f"lab/verdicts/{loop}.tsv"


def role_path(judge: str) -> str:
    return f".factory/roles/{judge}.md"


def retention_path(post: str) -> str:
    return f"distribution/{post}/retention.json"


def _read(base, rel: str) -> str:
    """The text of `rel` under `base`; `AgreementError("<rel> cannot be read")` when it cannot be decoded."""
    try:
        return (Path(base) / rel).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise AgreementError(f"{rel} cannot be read") from None


def _lines(text: str) -> list[tuple[int, str]]:
    """The non-empty lines of `text` with their 1-based line numbers."""
    return [(n, line) for n, line in enumerate(text.splitlines(), 1) if line.strip()]


def _shaped(spec) -> bool:
    if not isinstance(spec, dict) or sorted(spec) != list(KEYS):
        return False
    episodes, judge, platform = spec["episodes"], spec["judge"], spec["platform"]
    return (isinstance(episodes, list) and len(episodes) >= 2
            and all(isinstance(e, str) and _EPISODE.fullmatch(e) for e in episodes)
            and len(set(episodes)) == len(episodes)
            and isinstance(judge, str) and _JUDGE.fullmatch(judge) is not None
            and isinstance(platform, str) and _PLATFORM.fullmatch(platform) is not None)


def held_out(root, loop: str) -> dict:
    """The held-out list of `loop` under `root`; `AgreementError` when the id, the file or its shape is wrong."""
    check_loop(loop)
    rel = held_out_path(loop)
    if not (Path(root) / rel).is_file():
        raise AgreementError(f"{rel} is missing")
    try:
        spec = json.loads(_read(root, rel))
    except ValueError:
        spec = None
    if not _shaped(spec):
        raise AgreementError(f"{rel} is not {{{', '.join(KEYS)}}}")
    return spec


def retention(root, post: str) -> decimal.Decimal:
    """The real retention of `post` under `root`: the retention of its last beat."""
    try:
        rows = calibration.rows(root, post)
    except calibration.CalibrationError as e:
        raise AgreementError(e.problems[0]) from None
    except (OSError, ValueError):
        raise AgreementError(f"{retention_path(post)} cannot be read") from None
    last = rows[-1]
    cell = last[calibration.HEADER.index("retention")]
    if cell == "-":
        raise AgreementError(f"post {post} has no retention on its last beat {last[calibration.HEADER.index('beat')]}")
    return decimal.Decimal(cell)


def guard(tree, loop: str, spec: dict) -> None:
    """`AgreementError` when the tree measured lets the tuned judge read a held-out episode's retention."""
    episodes = set(spec["episodes"])
    if (Path(tree) / calibration.PATH).is_file():
        for n, line in _lines(_read(tree, calibration.PATH))[1:]:
            post = line.split("\t")[0]
            if post.split("/")[0] in episodes:
                raise AgreementError(f"{calibration.PATH} line {n}: post {post} is a held-out episode of loop {loop}")
    rel = role_path(spec["judge"])
    if not (Path(tree) / rel).is_file():
        return
    try:
        meta, _ = frontmatter.parse(_read(tree, rel))
    except frontmatter.FrontMatterError as e:
        raise AgreementError(f"{rel}: {e}") from None
    reads = frontmatter.as_list(meta.get("reads"))
    for episode in sorted(episodes):
        path = retention_path(f"{episode}/{spec['platform']}")
        if production.matches(path, reads):
            raise AgreementError(f"{rel} reads {path}, the retention of a held-out episode")


def verdicts(tree, loop: str, spec: dict) -> list[tuple[str, str, str]]:
    """The verdicts `(a, b, marker)` of `lab/verdicts/<loop>.tsv` in the tree measured, in file order."""
    rel = verdicts_path(loop)
    if not (Path(tree) / rel).is_file():
        raise AgreementError(f"{rel} is missing")
    lines = _lines(_read(tree, rel))
    if not lines or lines[0][1] != "\t".join(VERDICT_HEADER):
        raise AgreementError(f"{rel} line 1: header is not {', '.join(VERDICT_HEADER)}")
    episodes = set(spec["episodes"])
    seen = {}
    out = []
    for n, line in lines[1:]:
        where = f"{rel} line {n}: "
        cells = line.split("\t")
        if len(cells) != len(VERDICT_HEADER):
            raise AgreementError(where + f"{len(cells)} cells, not {len(VERDICT_HEADER)}")
        judge, a, b, marker = cells
        if judge != spec["judge"]:
            raise AgreementError(where + f"judge {judge} is not the tuned judge {spec['judge']}")
        for episode in (a, b):
            if episode not in episodes:
                raise AgreementError(where + f"episode {episode} is not held out")
        if a == b:
            raise AgreementError(where + f"a and b are both {a}")
        if marker not in MARKERS:
            raise AgreementError(where + f"marker {marker} is not PREFERS A, PREFERS B or NO PREFERENCE")
        pair = tuple(sorted((a, b)))
        if pair in seen:
            raise AgreementError(where + f"the pair {pair[0]}, {pair[1]} is repeated (first on line {seen[pair]})")
        seen[pair] = n
        out.append((a, b, marker))
    return out


def measure(root, tree, loop: str) -> dict:
    """The agreement of `loop`: `{"judge", "agree", "counted", "equal", "value"}`; raises `AgreementError`."""
    spec = held_out(root, loop)
    guard(tree, loop, spec)
    real = {e: retention(root, f"{e}/{spec['platform']}") for e in spec["episodes"]}
    agree = counted = equal = 0
    for a, b, marker in verdicts(tree, loop, spec):
        if real[a] == real[b]:
            equal += 1
            continue
        counted += 1
        if (marker == "PREFERS A" and real[a] > real[b]) or (marker == "PREFERS B" and real[b] > real[a]):
            agree += 1
    if not counted:
        raise AgreementError(f"{verdicts_path(loop)}: no verdict on two held-out episodes of different retention")
    return {"judge": spec["judge"], "agree": agree, "counted": counted, "equal": equal,
            "value": round(agree / counted, PLACES)}
