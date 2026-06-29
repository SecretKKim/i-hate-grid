"""Frameless, classic-looking grid window.

- No title bar (clean), but a normal light/white table — easy on the eyes.
- Drag anywhere on the panel to move it; clicking outside the window closes it.
- Excel-like cell/column selection (click a column header = whole column).
- Ctrl+C copies the selection as TSV (pastes straight into Excel).
- Double-click a cell to fix an OCR typo before copying.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import (
    QColor,
    QKeySequence,
    QPainter,
    QPen,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

_TABLE_QSS = """
QTableWidget {
    background: #ffffff;
    color: #1e1e1e;
    gridline-color: #dcdcdc;
    border: none;
    font: 13px 'Segoe UI';
    selection-background-color: #cce4ff;
    selection-color: #000000;
}
QTableWidget::item { padding: 2px 6px; }
QHeaderView::section {
    background: #f3f3f3;
    color: #333333;
    border: 0px;
    border-right: 1px solid #e3e3e3;
    border-bottom: 1px solid #e3e3e3;
    padding: 4px 8px;
    font: 600 12px 'Segoe UI';
}
QTableCornerButton::section { background: #f3f3f3; border: 0; }
QScrollBar:vertical { background: transparent; width: 11px; margin: 0; }
QScrollBar:horizontal { background: transparent; height: 11px; margin: 0; }
QScrollBar::handle { background: #c4c4c4; border-radius: 5px; min-height: 24px; min-width: 24px; }
QScrollBar::handle:hover { background: #a8a8a8; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
"""

_CLOSE_QSS = """
QPushButton {
    background: #f0f0f0; color: #333333;
    border: 1px solid #cccccc; border-radius: 4px;
    font: 12px 'Segoe UI';
}
QPushButton:hover { background: #e5e5e5; }
"""


class GridWindow(QWidget):
    def __init__(self, tables: list[list[list[str]]], source: str = "") -> None:
        super().__init__()
        self._tables = tables or [[[""]]]
        self._grid = self._tables[0]
        self._drag_off: QPoint | None = None

        self.setWindowTitle("I hate Grid")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)  # for rounded corners
        self.resize(760, 460)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 10)
        root.setSpacing(7)

        # --- table -------------------------------------------------
        self._table = QTableWidget(self)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self._table.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
        )
        self._table.setWordWrap(True)
        self._table.setAlternatingRowColors(False)
        self._table.setFrameShape(QTableWidget.NoFrame)
        self._table.setStyleSheet(_TABLE_QSS)
        root.addWidget(self._table, 1)

        # --- small footer ------------------------------------------
        footer = QHBoxLayout()
        footer.setContentsMargins(2, 0, 2, 0)
        self._tag = QLabel("Drag and Copy!")
        self._tag.setStyleSheet("color: #888888; font: 600 12px 'Segoe UI';")
        footer.addWidget(self._tag)
        footer.addStretch(1)
        close = QPushButton("Close")
        close.setFixedSize(58, 22)
        close.setCursor(Qt.PointingHandCursor)
        close.setStyleSheet(_CLOSE_QSS)
        close.clicked.connect(self.close)
        footer.addWidget(close)
        root.addLayout(footer)

        QShortcut(QKeySequence.Copy, self._table).activated.connect(self.copy_selection)
        QShortcut(QKeySequence(Qt.Key_Escape), self).activated.connect(self.close)

        self._load()

    # ----------------------------------------------------------------
    def _load(self) -> None:
        grid = self._grid
        rows = len(grid)
        cols = max((len(r) for r in grid), default=0)
        self._table.clear()
        self._table.setRowCount(rows)
        self._table.setColumnCount(cols)
        self._table.setHorizontalHeaderLabels([_col_label(c) for c in range(cols)])
        for r in range(rows):
            for c in range(cols):
                text = grid[r][c] if c < len(grid[r]) else ""
                self._table.setItem(r, c, QTableWidgetItem(text))
        self._table.resizeColumnsToContents()
        self._table.resizeRowsToContents()

    # --- clean white card backdrop ----------------------------------
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(2, 2, -2, -2)
        p.setBrush(QColor("#ffffff"))
        p.setPen(QPen(QColor("#b8b8b8"), 1))
        p.drawRoundedRect(rect, 8, 8)
        p.end()

    # --- drag the frameless window ----------------------------------
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_off = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag_off is not None and (e.buttons() & Qt.LeftButton):
            self.move(e.globalPosition().toPoint() - self._drag_off)

    def mouseReleaseEvent(self, _e):
        self._drag_off = None

    # --- copy selection as TSV --------------------------------------
    def copy_selection(self) -> None:
        ranges = self._table.selectedRanges()
        if not ranges:
            r0, c0 = 0, 0
            r1 = self._table.rowCount() - 1
            c1 = self._table.columnCount() - 1
            selected = None
        else:
            r0 = min(rg.topRow() for rg in ranges)
            r1 = max(rg.bottomRow() for rg in ranges)
            c0 = min(rg.leftColumn() for rg in ranges)
            c1 = max(rg.rightColumn() for rg in ranges)
            selected = set()
            for rg in ranges:
                for r in range(rg.topRow(), rg.bottomRow() + 1):
                    for c in range(rg.leftColumn(), rg.rightColumn() + 1):
                        selected.add((r, c))

        lines: list[str] = []
        for r in range(r0, r1 + 1):
            cells: list[str] = []
            for c in range(c0, c1 + 1):
                if selected is None or (r, c) in selected:
                    item = self._table.item(r, c)
                    cells.append(_tsv_field(item.text() if item else ""))
                else:
                    cells.append("")
            lines.append("\t".join(cells))
        ok = _set_clipboard_text("\r\n".join(lines))

        n = f"{r1 - r0 + 1}×{c1 - c0 + 1}"
        self._tag.setText(f"Copied {n}  —  paste with Ctrl+V" if ok
                          else "Copy failed — try again")


def _set_clipboard_text(text: str) -> bool:
    """Put text on the Windows clipboard directly (CF_UNICODETEXT).

    Bypasses Qt's delayed-rendering clipboard, which could deliver truncated
    data to external apps (e.g. Excel). This physically owns the data, so it
    survives this window closing and pastes in full.
    """
    import ctypes
    from ctypes import wintypes

    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    k = ctypes.windll.kernel32
    u = ctypes.windll.user32
    u.OpenClipboard.argtypes = [wintypes.HWND]
    u.OpenClipboard.restype = wintypes.BOOL
    u.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    u.SetClipboardData.restype = wintypes.HANDLE
    k.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    k.GlobalAlloc.restype = wintypes.HANDLE
    k.GlobalLock.argtypes = [wintypes.HANDLE]
    k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalUnlock.argtypes = [wintypes.HANDLE]

    buf = ctypes.create_unicode_buffer(text)   # UTF-16, null-terminated
    size = ctypes.sizeof(buf)
    if not u.OpenClipboard(None):
        return False
    try:
        u.EmptyClipboard()
        h = k.GlobalAlloc(GMEM_MOVEABLE, size)
        if not h:
            return False
        ptr = k.GlobalLock(h)
        if not ptr:
            return False
        ctypes.memmove(ptr, buf, size)
        k.GlobalUnlock(h)
        if not u.SetClipboardData(CF_UNICODETEXT, h):
            return False
        return True
    finally:
        u.CloseClipboard()


def _tsv_field(s: str) -> str:
    """Quote a field that contains newlines/tabs so Excel keeps it in one cell."""
    if any(ch in s for ch in ("\t", "\n", "\r", '"')):
        return '"' + s.replace('"', '""') + '"'
    return s


def _col_label(n: int) -> str:
    """0->A, 25->Z, 26->AA ... (Excel-style column names)."""
    s = ""
    n += 1
    while n > 0:
        n, rem = divmod(n - 1, 26)
        s = chr(65 + rem) + s
    return s
