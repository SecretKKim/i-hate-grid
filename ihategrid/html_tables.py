"""Extract tables (2-D cell arrays) from clipboard HTML.

When you drag-copy a table from an email/web page, pasting it as text
collapses to one line — but the clipboard's 'text/html' (CF_HTML) still
holds the full <table> structure. This module pulls those tables out of
the HTML and rebuilds them as row/column grids.

rowspan/colspan are expanded so the result is a real rectangular grid.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
import re


@dataclass
class _Cell:
    text: str = ""
    rowspan: int = 1
    colspan: int = 1


@dataclass
class _RawTable:
    rows: list[list[_Cell]] = field(default_factory=list)


class _TableExtractor(HTMLParser):
    """Collect cells inside <table>. Nested tables are split by the outermost."""

    # inline tags treated as whitespace/line breaks inside cell text
    _BLOCK_BREAK = {"br", "p", "div", "li", "tr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[_RawTable] = []
        self._depth = 0          # table nesting depth
        self._cur: _RawTable | None = None
        self._cur_row: list[_Cell] | None = None
        self._cur_cell: _Cell | None = None
        self._buf: list[str] = []

    # --- helpers -------------------------------------------------
    @staticmethod
    def _int_attr(attrs: dict[str, str | None], name: str, default: int) -> int:
        v = attrs.get(name)
        if v is None:
            return default
        try:
            n = int(str(v).strip())
            return n if n > 0 else default
        except ValueError:
            return default

    def _flush_cell_text(self) -> str:
        text = "".join(self._buf)
        self._buf.clear()
        # normalize newlines / collapse runs of whitespace
        text = text.replace("\xa0", " ")
        text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
        text = re.sub(r"[ \t]+", " ", text)
        return text.strip()

    # --- parser hooks --------------------------------------------
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            self._depth += 1
            if self._depth == 1:
                self._cur = _RawTable()
            return
        if self._depth == 0 or self._cur is None:
            return
        if tag == "tr":
            self._cur_row = []
        elif tag in ("td", "th"):
            self._cur_cell = _Cell(
                rowspan=self._int_attr(a, "rowspan", 1),
                colspan=self._int_attr(a, "colspan", 1),
            )
            self._buf.clear()
        elif tag in self._BLOCK_BREAK and self._cur_cell is not None:
            self._buf.append("\n")

    def handle_endtag(self, tag):
        if tag == "table":
            if self._depth == 1 and self._cur is not None:
                if self._cur.rows:
                    self.tables.append(self._cur)
                self._cur = None
            self._depth = max(0, self._depth - 1)
            return
        if self._depth == 0 or self._cur is None:
            return
        if tag in ("td", "th") and self._cur_cell is not None:
            self._cur_cell.text = self._flush_cell_text()
            if self._cur_row is None:
                self._cur_row = []
            self._cur_row.append(self._cur_cell)
            self._cur_cell = None
        elif tag == "tr" and self._cur_row is not None:
            self._cur.rows.append(self._cur_row)
            self._cur_row = None

    def handle_data(self, data):
        if self._cur_cell is not None:
            self._buf.append(data)


def _expand(raw: _RawTable) -> list[list[str]]:
    """Expand rowspan/colspan into a rectangular grid of strings."""
    grid: list[list[str | None]] = []

    def ensure(r: int, c: int) -> None:
        while len(grid) <= r:
            grid.append([])
        row = grid[r]
        while len(row) <= c:
            row.append(None)

    for r, row in enumerate(raw.rows):
        c = 0
        for cell in row:
            # skip cells already filled by a rowspan from a row above
            ensure(r, c)
            while c < len(grid[r]) and grid[r][c] is not None:
                c += 1
            for dr in range(cell.rowspan):
                for dc in range(cell.colspan):
                    rr, cc = r + dr, c + dc
                    ensure(rr, cc)
                    # merged cell: text only in the top-left, rest blank
                    grid[rr][cc] = cell.text if (dr == 0 and dc == 0) else ""
            c += cell.colspan

    width = max((len(r) for r in grid), default=0)
    out: list[list[str]] = []
    for row in grid:
        out.append([(v if v is not None else "") for v in row] + [""] * (width - len(row)))
    return out


def extract_tables(html: str) -> list[list[list[str]]]:
    """HTML string -> list of tables. Each table is list[row][col]=str.

    Empty tables and 1x1 noise are excluded.
    """
    p = _TableExtractor()
    try:
        p.feed(html)
        p.close()
    except Exception:
        pass
    tables: list[list[list[str]]] = []
    for raw in p.tables:
        grid = _expand(raw)
        if not grid:
            continue
        rows, cols = len(grid), len(grid[0]) if grid else 0
        if rows * cols <= 1:
            continue
        # drop completely empty tables
        if not any(any(cell.strip() for cell in row) for row in grid):
            continue
        tables.append(grid)
    return tables
