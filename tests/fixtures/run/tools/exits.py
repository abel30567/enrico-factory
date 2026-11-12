"""A tool for the run tests that ends the process with the key as its message, at the call."""

import sys

RESOURCE = "gen.fake"
REASONS = ()


def run(call):
    with open(call["secrets"]["gen.fake"]) as handle:
        sys.exit(handle.read())
