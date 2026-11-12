"""factory media <put|link|check> [--root DIR]: put a file in the media store, print or check a public link.

`put <file> --id ID --source TEXT [--apply]` stores the file's bytes under their sha256 and, after the
public link served them back, appends the row `ID, sha256, bytes, TEXT` to `media/manifest.tsv` in the
working tree. Nothing is committed or pushed. Without `--apply` it makes every check and sends nothing.
`link ID` prints the public link of a row and sends nothing. `check [ID ...]` gets the public link of
the named rows, or of every row, and compares the bytes.

Prints only `media <id>: ...` lines and a link. Exit 0 done, 1 when the store or a link did not answer as
needed, 2 when refused before any request; the reason goes to standard error as `media: <reason>`.
"""

import argparse
import os
import re
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from kit.lib import media, production, resources  # noqa: E402

MANIFEST = "media/manifest.tsv"
HEADER = ["id", "sha256", "bytes", "source"]
RESOURCE = "store.media"
ID = re.compile(r"[a-z0-9][a-z0-9._-]*")


def _rows(root):
    """The manifest's rows as ``(id, sha256, bytes)``; an empty list when there is no manifest."""
    path = Path(root) / MANIFEST
    if not path.is_file():
        return []
    lines = [line.split("\t") for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines or [c.strip() for c in lines[0]] != HEADER:
        raise media.Refused(f"{MANIFEST}: bad header")
    return [tuple(c.strip() for c in fields[:3]) for fields in lines[1:] if len(fields) >= 3]


def _row(rows, mid):
    found = next((row for row in rows if row[0] == mid), None)
    if found is None:
        raise media.Refused(f"id {mid} is not in the manifest")
    return found


def _source_ok(source):
    lines = source.splitlines()
    return len(lines) == 1 and lines[0] == source and "\t" not in source and source.strip() != ""


def _append(root, row):
    path = Path(root) / MANIFEST
    text = path.read_text(encoding="utf-8") if path.is_file() else "\t".join(HEADER) + "\n"
    if not text.endswith("\n"):
        text += "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\t".join(row) + "\n", encoding="utf-8")


def _put(root, values, args):
    register, problems = resources.load(resources.REGISTER)
    if problems:
        raise media.Refused("; ".join(problems))
    resource = next(r for r in register["resources"] if r["id"] == RESOURCE)
    ok, reasons = resources.check(resource, values, register, root)
    if not ok:
        raise media.Refused(f"resource {RESOURCE} is not usable: {'; '.join(reasons)}")
    media.store_base(values)
    media.public_base(values)
    key_file = os.path.abspath(os.path.join(resources.expand(values[register["secrets_dir_setting"]]), RESOURCE))
    media.read_key(key_file)
    sha256, size = media.digest(args.file)
    if not ID.fullmatch(args.id):
        raise media.Refused("id must match [a-z0-9][a-z0-9._-]*")
    if not _source_ok(args.source):
        raise media.Refused("source must be one line with no tab")
    same = [row for row in _rows(root) if row[0] == args.id]
    if any(row != (args.id, sha256, str(size)) for row in same):
        raise media.Refused(f"id {args.id} is in the manifest with other bytes")
    if not args.apply:
        print(f"media {args.id}: would store {sha256} {size}")
        return 0
    stored = media.put(values, key_file, args.file)
    if not same:
        _append(root, (args.id, stored["sha256"], str(stored["bytes"]), args.source))
    print(f"media {args.id}: STORED {stored['sha256']} {stored['bytes']} {stored['link']}"
          + (" (already recorded)" if same else ""))
    return 0


def _link(root, values, args):
    media.public_base(values)
    print(media.link(values, _row(_rows(root), args.id)[1]))
    return 0


def _check(root, values, args):
    media.public_base(values)
    rows = _rows(root)
    ids = sorted(set(args.ids)) if args.ids else sorted({row[0] for row in rows})
    chosen = [_row(rows, mid) for mid in ids]
    code = 0
    for mid, sha256, size in chosen:
        try:
            media.check(values, sha256, int(size) if size.isdigit() else -1)
        except media.Failed as failure:
            print(f"media {mid}: FAILED {failure}")
            code = 1
        else:
            print(f"media {mid}: OK")
    return code


def main(argv):
    parser = argparse.ArgumentParser(prog="factory media", description=__doc__.split(": ", 1)[1].split("\n")[0])
    parser.add_argument("--root", default=".", help="a directory inside the production (default: .)")
    forms = parser.add_subparsers(dest="form", required=True)
    put = forms.add_parser("put", help="store a file's bytes and add its manifest row")
    put.add_argument("file", help="the file to store")
    put.add_argument("--id", required=True, help="the manifest row's id")
    put.add_argument("--source", required=True, help="where the file came from, one line")
    put.add_argument("--apply", action="store_true", help="send the requests and add the row")
    link = forms.add_parser("link", help="print the public link of a manifest row")
    link.add_argument("id", help="the manifest row's id")
    check = forms.add_parser("check", help="check that public links serve the manifest's bytes")
    check.add_argument("ids", nargs="*", metavar="id", help="the rows to check (default: every row)")
    for form in (put, link, check):
        form.add_argument("--root", default=argparse.SUPPRESS, help="a directory inside the production")
    args = parser.parse_args(argv)
    try:
        root = production.find_root(args.root)
        values = resources.settings(root)
        return {"put": _put, "link": _link, "check": _check}[args.form](root, values, args)
    except (media.Refused, production.ProductionError) as error:
        print(f"media: {error}", file=sys.stderr)
        return 2
    except media.Failed as failure:
        print(f"media: {failure}", file=sys.stderr)
        return 1
    except (OSError, UnicodeDecodeError):
        print("media: a file could not be read or written", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
