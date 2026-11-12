"""A tool for the run tests whose resource is not in the register."""

RESOURCE = "gen.other"
REASONS = ()


def run(call):
    return {"usd": 0, "compute_min": 0}
