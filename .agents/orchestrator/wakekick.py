# wakekick.py — lets the mechanical tick start the judgement wake as soon as something new needs it,
# instead of leaving it for the wake's next timer run. ORCHESTRATOR ONLY. No model runs here.
# tick.py calls note() once per --apply run with the NEED lines it printed:
#   - a NEED line that the previous tick did not print is new. It is appended to STATE/state/wake.pending,
#     unless the wake itself ran this tick (ORCH_WAKE=1): then the session is reading the line as it works.
#   - when wake.pending is not empty and no wake is alive, the wake is started now. A wake that is alive
#     keeps the lines pending, and the first tick after it ends starts the next one.
# wake.sh takes wake.pending when it starts and puts the lines in the session's prompt. So a new NEED line
# starts at most one extra wake, and a NEED line that stays the same never starts a second one.
import re


def key(line):
    """A NEED line without the numbers that change from tick to tick while it means the same thing."""
    return re.sub(r"\b\d+ (min|work stream)", r"N \1", line)


def _lines(path):
    try:
        return [l for l in path.read_text().splitlines() if l]
    except OSError:
        return []


def note(needs, state, in_wake, wake_alive, kick):
    """Record this tick's NEED lines; return how many pending lines a wake was started for (0: none started)."""
    seen_f, pend_f = state / "needs.seen", state / "wake.pending"
    seen = set(_lines(seen_f))
    seen_f.write_text("".join(key(l) + "\n" for l in needs))
    if in_wake:
        return 0
    pending = _lines(pend_f)
    held = {key(l) for l in pending}
    new = [l for l in needs if key(l) not in seen and key(l) not in held]
    if new:
        pending += new
        pend_f.write_text("".join(l + "\n" for l in pending))
    if pending and not wake_alive and kick():
        return len(pending)
    return 0
