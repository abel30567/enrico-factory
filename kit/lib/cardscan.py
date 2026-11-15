"""Card scans: whether a reference image is framed like a trading card rather than a picture of its
subject alone (brief section 16, "Requests for generated media").

`decode` turns an image into `SIZE` by `SIZE` pixels of `rgb24` with `ffmpeg`, through the ``run`` it is
given; `art_region` finds an art window with straight edges and plain sides above a text panel with
marks. The module writes no file and reads no setting.
"""

import subprocess

SIZE = 160
DECODE_S = 60
DECODE = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-frames:v", "1",
          "-vf", "scale=160:160", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
EDGE = 32
MARGIN = 4
LINE = 0.5
SIDE = 0.9
KEEP = 24
PLAIN = 0.10
TEXT = 0.04


def decode(data, run):
    """The `SIZE` by `SIZE` `rgb24` pixels of the image ``data``, or None when it does not decode."""
    try:
        done = run(DECODE, input=data, capture_output=True, timeout=DECODE_S)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    out = getattr(done, "stdout", None)
    if getattr(done, "returncode", None) != 0 or not isinstance(out, bytes) or len(out) != SIZE * SIZE * 3:
        return None
    return out


def _prefix(values):
    sums = [0]
    for value in values:
        sums.append(sums[-1] + value)
    return sums


def _lines(counts, span):
    """The `KEEP` positions of highest count (tie: lower position) with ``counts[i]`` at least `LINE` of
    ``span``, sorted."""
    found = sorted((-c, i) for i, c in counts if c >= LINE * span)[:KEEP]
    return sorted(i for _, i in found)


def _share(cells, area):
    return 1.0 if area <= 0 else cells / area


def art_region(rgb):
    """``(x0, y0, x1, y1)`` of the largest art window framed like a card in the `SIZE` by `SIZE` pixels
    ``rgb``, or None."""
    n = SIZE
    if len(rgb) != n * n * 3:
        return None
    px = [[tuple(rgb[3 * (y * n + x):3 * (y * n + x) + 3]) for x in range(n)] for y in range(n)]

    def differ(a, b):
        return abs(a[0] - b[0]) >= EDGE or abs(a[1] - b[1]) >= EDGE or abs(a[2] - b[2]) >= EDGE

    h = [[y >= 1 and differ(px[y][x], px[y - 1][x]) for x in range(n)] for y in range(n)]
    v = [[x >= 1 and differ(px[y][x], px[y][x - 1]) for x in range(n)] for y in range(n)]
    h_rows = [_prefix(h[y]) for y in range(n)]                       # h_rows[y][x]: h at y, x in [0, x)
    v_cols = [_prefix(v[y][x] for y in range(n)) for x in range(n)]  # v_cols[x][y]: v at x, y in [0, y)
    area = [[0] * (n + 1) for _ in range(n + 1)]                      # area[y][x]: v in [0, y) x [0, x)
    for y in range(n):
        for x in range(n):
            area[y + 1][x + 1] = area[y][x + 1] + area[y + 1][x] - area[y][x] + v[y][x]

    def v_cells(xa, xb, ya, yb):
        if xb <= xa or yb <= ya:
            return 0, 0
        return area[yb][xb] - area[ya][xb] - area[yb][xa] + area[ya][xa], (xb - xa) * (yb - ya)

    lo, hi = MARGIN, n - MARGIN
    rows = _lines([(y, h_rows[y][hi] - h_rows[y][lo]) for y in range(lo, hi + 1)], hi - lo)
    cols = _lines([(x, v_cols[x][hi] - v_cols[x][lo]) for x in range(lo, hi + 1)], hi - lo)
    best = None
    for i, y0 in enumerate(rows):
        for y1 in rows[i + 1:]:
            t = y1 - y0
            if t < 0.2 * n or n - MARGIN - y1 < 0.15 * n:
                continue
            for j, x0 in enumerate(cols):
                for x1 in cols[j + 1:]:
                    w = x1 - x0
                    if w < 0.6 * n or (best is not None and (-(w * t), y0, x0) >= best[0]):
                        continue
                    if (h_rows[y0][x1] - h_rows[y0][x0] < SIDE * w or h_rows[y1][x1] - h_rows[y1][x0] < SIDE * w
                            or v_cols[x0][y1] - v_cols[x0][y0] < SIDE * t
                            or v_cols[x1][y1] - v_cols[x1][y0] < SIDE * t):
                        continue
                    if (_share(*v_cells(1, x0 - 1, y0, y1)) > PLAIN
                            or _share(*v_cells(x1 + 2, n, y0, y1)) > PLAIN):
                        continue
                    if _share(*v_cells(x0 + 1, x1, y1 + 2, n - MARGIN)) < TEXT:
                        continue
                    best = ((-(w * t), y0, x0), (x0, y0, x1, y1))
    return None if best is None else best[1]
