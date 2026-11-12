"""A tool for the run tests that does not follow the interface: it has no REASONS."""

RESOURCE = "gen.fake"


def run(call):
    return {"usd": 0, "compute_min": 0}
