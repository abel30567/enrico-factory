"""factory assets <put|get|check> [--root DIR]: put an engine binary in the asset store, get it back, check it.

`put <file> [--apply]` stores the file's bytes under their sha256 unless the store holds them; without
`--apply` it makes every check and sends nothing. `get <sha256> <path>` writes the object's bytes to a new
file at `path` and never writes over a file. `check <sha256> ...` asks the store for each object's length.
Every form needs the key file `<SECRETS_DIR>/store.assets`. No manifest is read or written, and nothing is
committed.

Prints only `assets <sha256>: ...` lines. Exit 0 done, 1 when a request was sent and the store did not
answer as needed or a `check` line is FAILED, 2 when refused before any request; the reason goes to
standard error as `assets: <reason>`.
"""

import argparse
import os
import sys

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from kit.lib import assets, media, production, resources  # noqa: E402

RESOURCE = "store.assets"


def _key_file(root, values):
    """The key file's path once `store.assets` passes its register checks."""
    register, problems = resources.load(resources.REGISTER)
    if problems:
        raise media.Refused("; ".join(problems))
    resource = next(r for r in register["resources"] if r["id"] == RESOURCE)
    ok, reasons = resources.check(resource, values, register, root)
    if not ok:
        raise media.Refused(f"resource {RESOURCE} is not usable: {'; '.join(reasons)}")
    return os.path.abspath(os.path.join(resources.expand(values[register["secrets_dir_setting"]]), RESOURCE))


def _put(root, values, args):
    key_file = _key_file(root, values)
    if not args.apply:
        _, _, sha256, size = assets.prepare(values, key_file, args.file)
        print(f"assets {sha256}: would store {size}")
        return 0
    stored = assets.put(values, key_file, args.file)
    print(f"assets {stored['sha256']}: STORED {stored['bytes']}" + ("" if stored["sent"] else " (already stored)"))
    return 0


def _get(root, values, args):
    fetched = assets.get(values, _key_file(root, values), args.sha256, args.path)
    print(f"assets {fetched['sha256']}: FETCHED {fetched['bytes']}" + ("" if fetched["sent"] else " (already there)"))
    return 0


def _check(root, values, args):
    key_file = _key_file(root, values)
    assets.store_base(values)
    assets.read_key(key_file)
    hashes = sorted({assets.require_sha256(value) for value in args.hashes})
    code = 0
    for sha256 in hashes:
        try:
            size = assets.check(values, key_file, sha256)
        except media.Failed as failure:
            print(f"assets {sha256}: FAILED {failure}")
            code = 1
        else:
            print(f"assets {sha256}: OK {size}")
    return code


def main(argv):
    parser = argparse.ArgumentParser(prog="factory assets", description=__doc__.split(": ", 1)[1].split("\n")[0])
    parser.add_argument("--root", default=".", help="a directory inside the production (default: .)")
    forms = parser.add_subparsers(dest="form", required=True)
    put = forms.add_parser("put", help="store a file's bytes under their sha256")
    put.add_argument("file", help="the file to store")
    put.add_argument("--apply", action="store_true", help="send the requests")
    get = forms.add_parser("get", help="write an object's bytes to a new file")
    get.add_argument("sha256", help="the object's sha256")
    get.add_argument("path", help="where to write it; nothing may be there unless it holds those bytes")
    check = forms.add_parser("check", help="ask the store for each object's length")
    check.add_argument("hashes", nargs="+", metavar="sha256", help="the objects to check")
    for form in (put, get, check):
        form.add_argument("--root", default=argparse.SUPPRESS, help="a directory inside the production")
    args = parser.parse_args(argv)
    try:
        root = production.find_root(args.root)
        values = resources.settings(root)
        return {"put": _put, "get": _get, "check": _check}[args.form](root, values, args)
    except (media.Refused, production.ProductionError) as error:
        print(f"assets: {error}", file=sys.stderr)
        return 2
    except media.Failed as failure:
        print(f"assets: {failure}", file=sys.stderr)
        return 1
    except (OSError, UnicodeDecodeError):
        print("assets: a file could not be read or written", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
