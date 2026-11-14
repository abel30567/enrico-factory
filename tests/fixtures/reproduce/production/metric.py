"""A fake perceptual metric for tests/test_reproduce.py: `python3 metric.py <first frame> <second frame>`.

A frame is an 8-bit binary PGM with an 11-byte header. Prints 10 less 10 times the mean absolute pixel
difference over 255, higher is better. Run only on the fixture frames; it checks nothing else.
"""

import sys

HEADER = 11


def main(first: str, second: str) -> None:
    with open(first, "rb") as handle:
        a = handle.read()[HEADER:]
    with open(second, "rb") as handle:
        b = handle.read()[HEADER:]
    total = sum(abs(x - y) for x, y in zip(a, b))
    count = len(a)
    print(f"{10 - 10 * total / count / 255:.4f}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
