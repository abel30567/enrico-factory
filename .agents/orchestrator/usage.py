# usage.py [--post [--dry-run]] — what every worker run used: turns, minutes, tokens and dollars.
#   orch usage          the local table, from each worker's JSON result in $STATE/runs
#   orch usage --post   one `[ORCHESTRATOR] USAGE <role> <id>` comment for each finished run that has none yet, on
#                       the issue or pull request its LAUNCH record is on (launches.tsv, column 7). tick.py runs it
#                       on every --apply tick, launch.sh before it overwrites a run's JSON, and scout.py after it
#                       collects a drafter, so GitHub holds what every run used. The comment is a record, not a
#                       ruling: no gate reads it, and tick.py does not count it as a ruling on a BLOCKED issue.
#   The first --post on a state directory records the runs already there as posted and posts nothing.
#   A local run (Claude Code JSON) carries tokens and total_cost_usd. A fleet run carries what the fleet reports:
#   inference_usd and cost_usd (the box), and no tokens.
import datetime as dt, fcntl, hashlib, json, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conf import R, STATE  # noqa: E402

TOKENS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def read(f):
    """The usage of one finished run, or None while it has no JSON result."""
    try:
        d = json.loads(f.read_text().strip().splitlines()[-1])
    except (ValueError, IndexError, OSError):
        return None
    if not isinstance(d, dict):
        return None
    u, fl = d.get("usage") or {}, d.get("fleet") or {}
    role, _, wid = f.stem.partition("-")
    r = {"role": role, "id": wid, "model": ",".join((d.get("modelUsage") or {}).keys()), "where": "fleet" if fl else "local",
         "turns": d.get("num_turns") or 0, "minutes": round((d.get("duration_ms") or 0) / 60000, 1),
         "result": "error" if d.get("is_error") else "ok",
         "inference_usd": d.get("total_cost_usd", fl.get("inference_usd")), "compute_usd": fl.get("cost_usd"),
         "instance": fl.get("instance_type"), "restarts": fl.get("restart_count"), "exit": fl.get("exit_reason"),
         "ended": dt.datetime.fromtimestamp(f.stat().st_mtime, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    r.update({k: u.get(k) for k in TOKENS})
    return r


def launched():
    """(role, id) -> (model, target) of its latest launch; target is `issue:N` or `pr:N`."""
    out, f = {}, STATE / "launches.tsv"
    for line in f.read_text().splitlines() if f.exists() else []:
        c = line.split("\t") + [""] * 7
        out[(c[1], c[2])] = (c[4], c[6] or (f"pr:{c[3]}" if c[3] else ""))
    return out


def usd(x):
    return "-" if x is None else f"${x:.4f}"


def body(r):
    if r["where"] == "fleet":
        used = (f"Fleet box {r['instance'] or '?'}, {r['restarts'] or 0} restart(s), ended `{r['exit'] or '?'}`: "
                f"inference {usd(r['inference_usd'])} · compute {usd(r['compute_usd'])} · tokens not reported by the fleet")
    else:
        used = ("Local run: " + " · ".join(f"{k.replace('_tokens', '').replace('_', ' ')} {r[k] or 0:,}" for k in TOKENS)
                + f" tokens · inference {usd(r['inference_usd'])}")
    return (f"[ORCHESTRATOR] USAGE {r['role']} {r['id']}\n"
            f"Model `{r['model'] or '?'}` · {r['turns']} turns · {r['minutes']} min · result {r['result']} · ended {r['ended']}\n"
            f"{used}\n"
            "A record of what this run used, not a ruling: no gate reads it. A dollar figure is the one the harness or "
            "the fleet reports for the run; on a subscription account it is an equivalent, not a bill.\n\n"
            f"```json\n{json.dumps(r, sort_keys=True)}\n```\n")


def post(dry):
    led = STATE / "state" / "usage-posted.json"
    with open(STATE / "state" / "usage.lock", "w") as lock:   # the tick and a hand launch may run this together
        fcntl.flock(lock, fcntl.LOCK_EX)
        seed = not led.exists()
        done = {} if seed else json.loads(led.read_text())
        la, n = launched(), 0
        for f in sorted((STATE / "runs").glob("*.json"), key=lambda p: p.stat().st_mtime):
            r = read(f)
            if not r:
                continue
            h = hashlib.sha256(f.read_bytes()).hexdigest()
            if done.get(f.stem) == h:
                continue
            if seed:
                done[f.stem] = h; n += 1; continue
            model, target = la.get((r["role"], r["id"]), ("", ""))
            kind, _, num = target.partition(":")
            if kind not in ("issue", "pr") or not num.isdigit():
                continue   # launched before launches.tsv had column 7, and not on a pull request
            r["model"] = r["model"] or model
            if dry:
                print(f"--- {kind} #{num}\n{body(r)}"); continue
            p = subprocess.run(["gh", kind, "comment", num, "-R", R, "--body-file", "-"], input=body(r),
                               capture_output=True, text=True)
            if p.returncode:   # left unrecorded: the next run tries again
                print(f"usage: gh {kind} comment {num} failed: {p.stderr.strip()[:200]}"); continue
            done[f.stem] = h
            led.write_text(json.dumps(done, indent=0, sort_keys=True))
            print(f"usage: posted {r['role']} {r['id']} on {kind} #{num}")
        if seed:
            if not dry:
                led.write_text(json.dumps(done, indent=0, sort_keys=True))
            print(f"usage: {'would record' if dry else 'recorded'} {n} earlier run(s) as posted; nothing posted")


def table():
    rows, tot = [], {"in": 0, "out": 0, "cr": 0, "turns": 0, "min": 0.0, "inf": 0.0, "box": 0.0}
    for f in sorted((STATE / "runs").glob("*.json"), key=lambda p: p.stat().st_mtime):
        r = read(f)
        if not r:
            rows.append((f.stem, "-", 0, 0, 0, 0, 0.0, 0.0, 0.0, "no JSON result")); continue
        row = (f.stem, r["model"] or r["where"], r["input_tokens"] or 0, r["output_tokens"] or 0,
               r["cache_read_input_tokens"] or 0, r["turns"], r["minutes"], r["inference_usd"] or 0.0,
               r["compute_usd"] or 0.0, r["result"])
        rows.append(row)
        for k, i in (("in", 2), ("out", 3), ("cr", 4), ("turns", 5), ("min", 6), ("inf", 7), ("box", 8)):
            tot[k] += row[i]
    print(f"{'run':22} {'model':18} {'input':>10} {'output':>8} {'cache-rd':>10} {'turns':>5} {'min':>6} {'infer $':>8} {'box $':>7}  result")
    for r in rows:
        print(f"{r[0]:22} {r[1][:18]:18} {r[2]:>10,} {r[3]:>8,} {r[4]:>10,} {r[5]:>5} {r[6]:>6} {r[7]:>8.2f} {r[8]:>7.2f}  {r[9]}")
    print(f"{'TOTAL':22} {'':18} {tot['in']:>10,} {tot['out']:>8,} {tot['cr']:>10,} {tot['turns']:>5} {tot['min']:>6.1f} "
          f"{tot['inf']:>8.2f} {tot['box']:>7.2f}")


if __name__ == "__main__":
    post("--dry-run" in sys.argv) if "--post" in sys.argv else table()
