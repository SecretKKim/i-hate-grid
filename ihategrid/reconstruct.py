"""Rebuild a table grid from positioned text boxes.

Given a list of words (text + bounding box) — wherever they came from (here,
UI Automation text elements) — reconstruct the rows and columns:
  - cluster words by y into rows
  - split columns at vertical whitespace gutters that are empty on *most* rows
    (so one long cell can't merge two columns)
  - the "key" columns (those that appear on ~every row, i.e. don't wrap) define
    where a new row starts; other lines are wrapped continuations merged into
    the cell above with a newline.
"""
from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass


@dataclass
class Word:
    text: str
    x: float
    y: float
    w: float
    h: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2


def _cluster_rows(words: list[Word]) -> list[list[Word]]:
    """Group words into rows by their y position."""
    if not words:
        return []
    med_h = statistics.median(w.h for w in words)
    tol = max(6.0, med_h * 0.6)
    ordered = sorted(words, key=lambda w: w.y + w.h / 2)
    rows: list[list[Word]] = []
    cur: list[Word] = []
    cur_cy = None
    for w in ordered:
        cy = w.y + w.h / 2
        if cur_cy is None or abs(cy - cur_cy) <= tol:
            cur.append(w)
            cur_cy = sum(x.y + x.h / 2 for x in cur) / len(cur)
        else:
            rows.append(cur)
            cur = [w]
            cur_cy = cy
    if cur:
        rows.append(cur)
    return rows


def _column_edges(row_groups: list[list[Word]], minx: float, span: int,
                  med_h: float) -> list[float]:
    """Column boundaries = vertical gutters empty on *most* rows (not all), so
    one row's long text can't erase a column boundary."""
    nlines = max(1, len(row_groups))
    colcount = [0] * span
    for ln in row_groups:
        marks = bytearray(span)
        for w in ln:
            a = max(0, int(round(w.x - minx)))
            b = min(span, int(round(w.x + w.w - minx)))
            for i in range(a, b):
                marks[i] = 1
        for i in range(span):
            if marks[i]:
                colcount[i] += 1

    thresh = max(0, int(nlines * 0.2))   # covered on <=20% of rows -> a gutter
    gutter_min = max(8.0, med_h * 0.6)

    boundaries: list[float] = []
    i = 0
    while i < span:
        if colcount[i] <= thresh:
            j = i
            while j < span and colcount[j] <= thresh:
                j += 1
            if (j - i) >= gutter_min:
                boundaries.append((i + j) / 2 + minx)
            i = j
        else:
            i += 1
    return boundaries


def lines_to_grid(lines: list[list[Word]]) -> list[list[str]]:
    """Word boxes -> rectangular grid of strings."""
    words = [w for ln in lines for w in ln]
    if not words:
        return []

    row_groups = _cluster_rows(words)
    med_h = statistics.median(w.h for w in words)
    minx = min(w.x for w in words)
    maxx = max(w.x + w.w for w in words)
    span = max(1, int(round(maxx - minx)))

    boundaries = _column_edges(row_groups, minx, span, med_h)
    edges = [-1e18] + boundaries + [1e18]
    ncols = len(edges) - 1

    def col_of(cx: float) -> int:
        for k in range(ncols):
            if edges[k] <= cx < edges[k + 1]:
                return k
        return ncols - 1

    # visual line -> text per column
    vis_lines = []
    for ln in row_groups:
        cells = [""] * ncols
        for w in sorted(ln, key=lambda z: z.x):
            k = col_of(w.cx)
            cells[k] = (cells[k] + " " + w.text).strip() if cells[k] else w.text
        filled = {k for k in range(ncols) if cells[k]}
        if filled:
            vis_lines.append({"cells": cells, "filled": filled})

    if not vis_lines:
        return []

    # Key columns: non-wrapping columns appear on ~every row, so their line
    # frequency equals the real row count. Wrapping columns appear more; stray
    # captured elements appear far less. Take the most common frequency as the
    # row count, and the columns near it as keys. A line that fills a key column
    # starts a new row; the rest (wrapped lines) merge into the row above.
    cnt = [0] * ncols
    for v in vis_lines:
        for k in v["filled"]:
            cnt[k] += 1
    positive = [c for c in range(ncols) if cnt[c] > 0]
    freq = Counter(cnt[c] for c in positive)
    rows_est = max(freq.items(), key=lambda kv: (kv[1], kv[0]))[0]
    anchors = {c for c in positive if rows_est * 0.6 <= cnt[c] <= rows_est * 1.5}
    if not anchors:
        anchors = set(positive)

    grid: list[list[str]] = []
    for v in vis_lines:
        starts_row = (not grid) or bool(v["filled"] & anchors)
        if starts_row:
            grid.append(list(v["cells"]))
        else:
            for k in v["filled"]:
                cur = grid[-1][k]
                grid[-1][k] = (cur + "\n" + v["cells"][k]) if cur else v["cells"][k]
    return grid
