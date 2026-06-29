"""Read the REAL text/structure under a screen point via UI Automation.

This is not OCR — it asks Windows for the actual characters an app exposes.

smart_extract_at(x, y) returns:
  - {'kind': 'table', 'grid': [[...]], 'via': ...}  the table the word belongs to
  - {'kind': 'none'}                                app exposes no text here

How a table is built:
  1. If the element sits in a real UIA grid (GridPattern), read its cells directly.
  2. Otherwise gather the text elements in the surrounding block and rebuild the
     grid from their box positions (handles PDF/PPT/Word text that isn't a formal
     UIA table).

Web / Electron apps (Chrome, Edge, new Outlook) usually keep their accessibility
tree off, so they return 'none' — use the clipboard (Ctrl+C) path for those.
"""
from __future__ import annotations

from .reconstruct import Word, lines_to_grid

_MAX_CELLS = 200000


def _auto():
    import uiautomation as auto
    return auto


def _grid_pattern(ctrl):
    """Return the GridPattern if this element is a real row/column grid."""
    try:
        gp = ctrl.GetGridPattern()
    except Exception:
        return None
    if gp is None:
        return None
    try:
        if gp.RowCount > 0 and gp.ColumnCount > 0:
            return gp
    except Exception:
        return None
    return None


def _extract_grid_pattern(gp) -> list[list[str]]:
    rows, cols = gp.RowCount, gp.ColumnCount
    if rows * cols > _MAX_CELLS:
        rows = min(rows, max(1, _MAX_CELLS // max(1, cols)))
    grid: list[list[str]] = []
    for r in range(rows):
        row: list[str] = []
        for c in range(cols):
            try:
                item = gp.GetItem(r, c)
                txt = (item.Name or "").strip() if item else ""
            except Exception:
                txt = ""
            row.append(txt)
        grid.append(row)
    return grid


def _collect_words(root, auto, limit: int = 6000) -> list[Word]:
    """Gather text elements (text + bounding box) under root."""
    words: list[Word] = []
    try:
        for ctrl, _depth in auto.WalkControl(root, includeTop=False, maxDepth=16):
            name = ctrl.Name
            if not name or not name.strip():
                continue
            try:
                r = ctrl.BoundingRectangle
            except Exception:
                continue
            if r.width() <= 0 or r.height() <= 0:
                continue
            words.append(Word(name.strip(), float(r.left), float(r.top),
                              float(r.width()), float(r.height())))
            if len(words) >= limit:
                break
    except Exception:
        pass
    return words


def _pick_container(ctrl, auto):
    """Walk up from the clicked element.

    Returns (grid_node, grid_pattern, generic_container):
      - if a real GridPattern/table is found, grid_pattern is set
      - otherwise generic_container is the smallest ancestor big enough to
        hold a table (used to gather text and rebuild the grid from boxes).
    """
    table_types = set()
    for nm in ("TableControl", "DataGridControl", "ListControl"):
        t = getattr(auto.ControlType, nm, None)
        if t is not None:
            table_types.add(t)

    try:
        wr = ctrl.BoundingRectangle
        word_area = max(1.0, float(wr.width()) * float(wr.height()))
    except Exception:
        word_area = 1.0

    generic = None
    node = ctrl
    for _ in range(16):
        if node is None:
            break
        gp = _grid_pattern(node)
        if gp is not None:
            return node, gp, None
        try:
            if node.ControlType in table_types:
                return node, None, node
        except Exception:
            pass
        # remember the first ancestor that's clearly bigger than one line
        if generic is None:
            try:
                r = node.BoundingRectangle
                if float(r.width()) * float(r.height()) >= word_area * 10:
                    generic = node
            except Exception:
                pass
        try:
            node = node.GetParentControl()
        except Exception:
            break
    return None, None, generic


def _looks_tabular(grid: list[list[str]]) -> bool:
    """True if the reconstructed grid has real table structure (>=2 columns
    each filled on >=2 rows) — filters out plain paragraphs."""
    if len(grid) < 2:
        return False
    cols = max((len(r) for r in grid), default=0)
    if cols < 2:
        return False
    colfill = [0] * cols
    for r in grid:
        for c in range(cols):
            if c < len(r) and r[c].strip():
                colfill[c] += 1
    return sum(1 for n in colfill if n >= 2) >= 2


def smart_extract_at(x: int, y: int) -> dict:
    try:
        auto = _auto()
        ctrl = auto.ControlFromPoint(x, y)
    except Exception:
        return {"kind": "none"}
    if ctrl is None:
        return {"kind": "none"}

    grid_node, gp, generic = _pick_container(ctrl, auto)

    # 1) a real grid (Office tables, data grids) -> exact cells
    if gp is not None:
        grid = _extract_grid_pattern(gp)
        if grid and any(any(c.strip() for c in row) for row in grid):
            return {"kind": "table", "grid": grid, "via": "GridPattern"}

    # 2) a table-type ancestor -> gather its text, rebuild grid from boxes
    if grid_node is not None:
        words = _collect_words(grid_node, auto)
        if len(words) >= 4:
            grid = lines_to_grid([words])
            if grid and any(any(c.strip() for c in row) for row in grid):
                return {"kind": "table", "grid": grid, "via": "text"}

    # 3) no formal table (PDF / PPT / Word text) -> rebuild from the
    #    surrounding block, but only accept if it really looks like a grid
    if generic is not None:
        words = _collect_words(generic, auto)
        if len(words) >= 4:
            grid = lines_to_grid([words])
            if _looks_tabular(grid):
                return {"kind": "table", "grid": grid, "via": "text"}

    return {"kind": "none"}
