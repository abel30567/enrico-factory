"""Publish: a post exists on its platform, private, processed, with the gated file, its captions and the
platform's disclosure (brief sections 6 and 16).

Rule ids: publish-upload, publish-exists, publish-answer, publish-job, publish-visibility, publish-processed,
publish-hash, publish-captions, publish-disclosure. The upload request of each post directory
`distribution/<ep>/<platform>/` (`upload.json`, a request as `kit/lib/jobs.py` names one) names `video` and
`captions` as `media:<id>`, ids of `media/manifest.tsv`. Its upload has run when `distribution/<ep>/<platform>/post.json`, the answer `factory run`
recorded (`kit/lib/posts.py`), exists: that answer names the job `jobs/<request path>` by its hash, is
private and processed, has the sha256 of the video and captions, and gives the disclosure field of
`rulebook/<platform>.json` its value. A rulebook that is missing, is not JSON, or has no disclosure field
and value is ERROR. Findings are on line 1. A tree with no `distribution/` passes.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .. import cmd_gate, jobs, posts
from ..lint import Finding, LintError, files, read_text, read_tsv

UPLOAD = "upload.json"
MEDIA = re.compile(r"media:(\S+)")
MANIFEST = "media/manifest.tsv"
RULEBOOK = "rulebook/{}.json"


def _json(root: Path, rel: str):
    try:
        return json.loads(read_text(root, rel))
    except ValueError:
        return None


def _manifest(root: Path) -> dict:
    """``{id: sha256}`` of the media manifest; ``{}`` without one."""
    if not (root / MANIFEST).is_file():
        return {}
    found = {}
    for _, fields in read_tsv(root, MANIFEST)[1:]:
        if len(fields) >= 2:
            found.setdefault(fields[0].strip(), fields[1].strip())
    return found


def _disclosure(root: Path, platform: str):
    """``(field, value)`` of the platform's rulebook; ``LintError`` when it has none."""
    rel = RULEBOOK.format(platform)
    reason = f"{rel} has no disclosure field and value"
    if not (root / rel).is_file():
        raise LintError(reason)
    doc = _json(root, rel)
    disclosure = doc.get("disclosure") if isinstance(doc, dict) else None
    field = disclosure.get("field") if isinstance(disclosure, dict) else None
    if not isinstance(field, str) or not field or disclosure.get("value") is None:
        raise LintError(reason)
    return field, disclosure["value"]


def _same(a, b) -> bool:
    """Equal JSON values: the same type and value."""
    return type(a) is type(b) and a == b


def _shown(value) -> str:
    return json.dumps(value, sort_keys=True)


def _is_upload(rel: str) -> bool:
    """``rel`` is the upload request of a post directory ``distribution/<ep>/<platform>/``."""
    parts = rel.split("/")
    return (len(parts) == 5 and parts[0] == posts.ROOM and parts[4] == UPLOAD
            and jobs.REQUEST.fullmatch(rel) is not None)


def _upload(root: Path, rel: str, media: dict) -> list[Finding]:
    _, episode, platform, *name = rel.split("/")
    request = _json(root, rel)
    ids = [MEDIA.fullmatch(request.get(k)) if isinstance(request, dict) and isinstance(request.get(k), str)
           else None for k in ("video", "captions")]
    if not all(ids):
        return [Finding(rel, 1, "publish-upload",
                        f"{'/'.join(name)} does not name video and captions as media:<id>")]
    video, captions = (m.group(1) for m in ids)
    missing = [Finding(rel, 1, "publish-upload", f"media:{mid} is not in {MANIFEST}")
               for mid in dict.fromkeys((video, captions)) if mid not in media]
    if missing:
        return missing
    answer_rel = posts.ANSWER.format(f"{episode}/{platform}")
    if not (root / answer_rel).is_file():
        return [Finding(rel, 1, "publish-exists", "no post.json for it: the upload has not run")]
    try:
        answer = json.loads(read_text(root, answer_rel))
    except ValueError:
        answer = None
    problems = posts.answer_problems(answer)
    if problems:
        return [Finding(answer_rel, 1, "publish-answer", problems[0])]

    def finding(rule, message):
        return Finding(answer_rel, 1, rule, message)

    found = []
    post = answer["post_id"]
    if answer["platform"] != platform:
        found.append(finding("publish-answer", f"platform {answer['platform']} is not {platform}"))
    job_rel = "jobs/" + rel
    if not (root / job_rel).is_file():
        found.append(finding("publish-job", f"no job file {job_rel}"))
    else:
        try:
            digest = cmd_gate._parsed(read_text(root, job_rel), job_rel)[1]
        except cmd_gate.Refused as error:
            raise LintError(str(error)) from None
        if answer["job"] != digest:
            found.append(finding("publish-job",
                                 f"post.json names job {answer['job'][:12]}, not the job {digest[:12]} of {job_rel}"))
    if answer["visibility"] != "private":
        found.append(finding("publish-visibility", f"post {post} is {answer['visibility']}, not private"))
    if not answer["processed"]:
        found.append(finding("publish-processed", f"post {post} is not processed"))
    if answer["sha256"] != media[video]:
        found.append(finding("publish-hash", f"post {post} has sha256 {answer['sha256'][:12]}, "
                                             f"not the {media[video][:12]} of media:{video}"))
    if not answer["captions"]:
        found.append(finding("publish-captions", f"post {post} has no captions"))
    field, value = _disclosure(root, platform)
    if field not in answer["fields"]:
        found.append(finding("publish-disclosure", f"post {post} has no disclosure field {field}"))
    elif not _same(answer["fields"][field], value):
        found.append(finding("publish-disclosure",
                             f"post {post} sets {field} to {_shown(answer['fields'][field])}, not {_shown(value)}"))
    return found


def check(root: Path) -> list[Finding]:
    uploads = sorted(rel for rel in (p.as_posix() for p in files(root)) if _is_upload(rel))
    if not uploads:
        return []
    media = _manifest(root)
    found = []
    for rel in uploads:
        found += _upload(root, rel, media)
    return found
