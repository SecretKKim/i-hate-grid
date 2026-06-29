"""I hate Grid — tray app + global Alt + double-click.

Alt + double-click anywhere on screen:
  1. If you double-clicked a word -> read the whole table it belongs to via
     Windows UI Automation (real text, exact, NOT OCR).
  2. If you double-clicked an image -> OCR that whole image.
  3. If the text can't be read (web / Electron apps) -> drag a region to OCR it.
Then select cells/columns -> Ctrl+C -> paste straight into Excel (TSV).
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import gc
import queue
import sys
import time

from PySide6.QtCore import (
    QObject,
    Qt,
    QThread,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QGuiApplication,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .grid_window import GridWindow
from .html_tables import extract_tables
from . import uia_table


class _UiaService(QThread):
    """One long-lived thread that owns all UI Automation calls.

    Why a dedicated persistent thread:
      - UIA is COM-based. A NEW thread per call makes the 2nd call fail (the
        COM client built in the 1st thread is dead in the next).
      - Running on Qt's main thread conflicts with Qt's own OLE apartment and
        fails entirely.
    One thread that initializes COM once and serves every request fixes both —
    reliable on the 2nd/3rd click, and never blocks the UI.
    """
    result = Signal(object)  # dict from uia_table.smart_extract_at

    def __init__(self) -> None:
        super().__init__()
        self._q: queue.Queue = queue.Queue()

    def request(self, x: int, y: int) -> None:
        self._q.put((x, y))

    def stop(self) -> None:
        self._q.put(None)

    def run(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                break
            x, y = item
            try:
                res = uia_table.smart_extract_at(x, y)
            except Exception:
                res = {"kind": "none"}
            self.result.emit(res)


# --- Win32 low-level mouse hook -------------------------------------
WH_MOUSE_LL = 14
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
HC_ACTION = 0
VK_MENU = 0x12  # Alt

LRESULT = ctypes.c_ssize_t
_user32 = ctypes.windll.user32
_user32.SetWindowsHookExW.restype = wintypes.HHOOK
_user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int, ctypes.c_void_p, wintypes.HINSTANCE, wintypes.DWORD,
]
_user32.CallNextHookEx.restype = LRESULT
_user32.CallNextHookEx.argtypes = [
    wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM,
]
_user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
_user32.GetAsyncKeyState.restype = ctypes.c_short
_user32.GetDoubleClickTime.restype = wintypes.UINT

_kernel32 = ctypes.windll.kernel32
_kernel32.GetCurrentProcess.restype = ctypes.c_void_p
_psapi = ctypes.windll.psapi
_psapi.EmptyWorkingSet.argtypes = [ctypes.c_void_p]


def _trim_memory() -> None:
    """Return freed pages to the OS so our RAM footprint stays tiny.
    Safe: pages page back in on demand; just trims the working set."""
    try:
        gc.collect()
        _psapi.EmptyWorkingSet(_kernel32.GetCurrentProcess())
    except Exception:
        pass


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


HOOKPROC = ctypes.CFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

_user32.GetMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
]
_user32.PostThreadMessageW.argtypes = [
    wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
]
_kernel32.GetCurrentThreadId.restype = wintypes.DWORD
WM_QUIT = 0x0012


class _HookThread(QThread):
    """The global mouse hook lives on its OWN thread with its own message loop.

    Why: a WH_MOUSE_LL hook runs on the thread that installed it, and Windows
    silently kills the hook if that thread ever stalls > ~300ms. On the GUI
    thread the first grab (building the grid window) can stall that long, which
    killed the hook — so the 2nd Alt+double-click was never seen. A dedicated
    thread that only pumps messages never stalls, so the hook stays alive.
    """
    doubleClicked = Signal(int, int)  # physical screen coords

    def __init__(self) -> None:
        super().__init__()
        self._tid = 0
        self._hook = None
        self._proc = HOOKPROC(self._on_event)
        self._last_t = 0.0
        self._last = (0, 0)
        self._dbl = max(200, int(_user32.GetDoubleClickTime()))

    def _on_event(self, nCode, wParam, lParam):
        if (
            nCode == HC_ACTION
            and wParam == WM_LBUTTONDOWN
            and (_user32.GetAsyncKeyState(VK_MENU) & 0x8000)
        ):
            pt = MSLLHOOKSTRUCT.from_address(lParam).pt
            x, y = int(pt.x), int(pt.y)
            now = time.monotonic()
            near = abs(x - self._last[0]) <= 6 and abs(y - self._last[1]) <= 6
            if (now - self._last_t) * 1000.0 <= self._dbl and near:
                self._last_t = 0.0
                self.doubleClicked.emit(x, y)
            else:
                self._last_t = now
                self._last = (x, y)
            return 1  # Alt+left-click isn't passed to the app below
        return _user32.CallNextHookEx(self._hook, nCode, wParam, lParam)

    def run(self) -> None:
        self._tid = _kernel32.GetCurrentThreadId()
        self._hook = _user32.SetWindowsHookExW(WH_MOUSE_LL, self._proc, None, 0)
        msg = wintypes.MSG()
        while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))
        if self._hook:
            _user32.UnhookWindowsHookEx(self._hook)
            self._hook = None

    def stop(self) -> None:
        if self._tid:
            _user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)


class AboutWindow(QWidget):
    """Frameless 'About' card. Witty, English, Tailgatelab."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("About — I hate Grid")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(380, 250)
        self._drag_off = None

        body = "font: 13px 'Segoe UI'; color: #444444;"

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 16)
        root.setSpacing(9)

        title = QLabel("I hate Grid 😈")
        title.setStyleSheet("font: 700 13px 'Segoe UI'; color: #1e1e1e;")  # only this bold
        sub = QLabel("Grab any table with Alt + double-click.")
        sub.setStyleSheet(body)

        phrase = QLabel("“Convenience always wins.”")
        phrase.setWordWrap(True)
        phrase.setStyleSheet(body)

        spread = QLabel("Free forever. Fork it, ship it, and spread it far and wide. 🚀")
        spread.setWordWrap(True)
        spread.setStyleSheet(body)

        root.addWidget(title)
        root.addWidget(sub)
        root.addSpacing(4)
        root.addWidget(phrase)
        root.addWidget(spread)
        root.addStretch(1)

        foot = QHBoxLayout()
        cr = QLabel(f"© 2026 Tailgatelab  ·  v{__version__}")
        cr.setStyleSheet(body)
        foot.addWidget(cr)

        help_badge = QLabel("!")
        help_badge.setFixedSize(16, 16)
        help_badge.setAlignment(Qt.AlignCenter)
        help_badge.setCursor(Qt.WhatsThisCursor)
        help_badge.setStyleSheet(
            "QLabel{background:#2d6cdf;color:white;border-radius:8px;"
            "font:700 11px 'Segoe UI';}"
        )
        help_badge.setToolTip(
            "<b>How to use</b><br><br>"
            "<b>1. Alt + double-click</b> a table<br>"
            "&nbsp;&nbsp;&nbsp;→ grabs the whole table (exact, no OCR)<br><br>"
            "<b>2. Select a table + Ctrl+C</b>, then click the 😈 icon<br>"
            "&nbsp;&nbsp;&nbsp;→ grabs it from the clipboard<br>"
            "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;(works in web mail / PDF / Word too)<br><br>"
            "<b>3. Alt + double-click → drag a box</b><br>"
            "&nbsp;&nbsp;&nbsp;→ OCR a screenshot or anything on screen<br><br>"
            "<i>In the grid:</i> click a column header to select it,<br>"
            "drag cells, then <b>Ctrl+C</b> → paste into Excel."
        )
        foot.addSpacing(6)
        foot.addWidget(help_badge)
        foot.addStretch(1)
        close = QPushButton("Close")
        close.setFixedSize(60, 24)
        close.setCursor(Qt.PointingHandCursor)
        close.setStyleSheet(
            "QPushButton{background:#f0f0f0;color:#333;border:1px solid #ccc;"
            "border-radius:4px;font:12px 'Segoe UI';}"
            "QPushButton:hover{background:#e5e5e5;}"
        )
        close.clicked.connect(self.close)
        foot.addWidget(close)
        root.addLayout(foot)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(2, 2, -2, -2)
        p.setBrush(QColor("#ffffff"))
        p.setPen(QPen(QColor("#b8b8b8"), 1))
        p.drawRoundedRect(rect, 10, 10)
        p.end()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_off = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag_off is not None and (e.buttons() & Qt.LeftButton):
            self.move(e.globalPosition().toPoint() - self._drag_off)

    def mouseReleaseEvent(self, _e):
        self._drag_off = None


class FloatingIcon(QWidget):
    """Pops next to the cursor when you copy a table — click to open the grid."""
    clicked = Signal()

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.resize(140, 38)
        self._hide = QTimer(self)
        self._hide.setSingleShot(True)
        self._hide.timeout.connect(self.hide)

    def pop_at(self, gp) -> None:
        self.move(gp.x() + 14, gp.y() + 14)
        self.show()
        self.raise_()
        self._hide.start(6000)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#2d6cdf"))
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 9, 9)
        p.setPen(QColor("white"))
        p.setFont(QFont("Segoe UI Emoji", 11))
        p.drawText(self.rect(), Qt.AlignCenter, "😈 Grab table")
        p.end()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.hide()
            self.clicked.emit()


class App(QObject):
    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self._app = app
        self._windows: list[GridWindow] = []
        self._busy = False  # one action at a time
        self._op_count = 0  # for periodic memory trim (every 3rd grab)
        self._hook_ok = True

        # global mouse hook on its own thread (survives UI stalls)
        self._hooks = _HookThread()
        self._hooks.doubleClicked.connect(self._on_double_click)
        self._hooks.start()

        # one persistent thread for all UIA reads (see _UiaService)
        self._uia = _UiaService()
        self._uia.result.connect(self._on_uia_result)
        self._uia.start()

        self._build_tray()

        # Selection path: copy a table (Ctrl+C) -> we parse the real HTML on
        # the clipboard (exact, no OCR). Works even in apps opaque to UIA.
        self._float = FloatingIcon()
        self._float.clicked.connect(self._open_clip_tables)
        self._pending_tables = None
        self._last_clip_html = ""
        app.clipboard().dataChanged.connect(self._on_clipboard_change)

        if self._hook_ok:
            QTimer.singleShot(400, lambda: self._tray.showMessage(
                "I hate Grid",
                "Ready 😈  Alt+double-click a table, or select it + Ctrl+C.",
                QSystemTrayIcon.Information, 3500,
            ))

    # --- selection -> clipboard HTML (exact, no OCR) ----------------
    def _on_clipboard_change(self) -> None:
        md = self._app.clipboard().mimeData()
        if md is None or not md.hasHtml():
            return  # our own grid copy is plain text only -> no loop
        html = md.html()
        if not html or html == self._last_clip_html:
            return
        # Only real grids (>=2 rows x >=2 cols) — ignore 1-col layout tables
        # that HTML emails use, so the icon shows up only for actual tables.
        tables = [t for t in extract_tables(html)
                  if len(t) >= 2 and max((len(r) for r in t), default=0) >= 2]
        if not tables:
            return
        self._last_clip_html = html
        self._pending_tables = tables
        self._float.pop_at(QCursor.pos())

    def _open_clip_tables(self) -> None:
        if self._pending_tables:
            self._show_tables(self._pending_tables, source="Clipboard · exact (HTML)")

    # --- Alt + double-click -> read the REAL text via UIA (no OCR) --
    def _on_double_click(self, x: int, y: int) -> None:
        if self._busy:
            return
        self._busy = True
        # watchdog: never get permanently stuck if a result is ever lost
        QTimer.singleShot(4000, self._clear_busy)
        self._uia.request(x, y)  # answered on _on_uia_result

    def _clear_busy(self) -> None:
        self._busy = False

    def _on_uia_result(self, res: dict) -> None:
        self._busy = False
        if res.get("kind") == "table":
            via = res.get("via", "accessibility")
            self._show_tables([res["grid"]], source=f"Accessibility · exact ({via})")
            return
        # Can't read this app (web/PDF). No OCR — point to the copy path.
        self._tray.showMessage(
            "I hate Grid",
            "Can't read this directly. Select the table + Ctrl+C instead.",
            QSystemTrayIcon.Information, 2800,
        )

    # --- Tray --------------------------------------------------------
    def _build_tray(self) -> None:
        self._tray = QSystemTrayIcon(_make_icon(), self)
        tip = "I hate Grid — Alt + double-click to grab a table/image"
        if not self._hook_ok:
            tip += "  (⚠ mouse hook failed)"
        self._tray.setToolTip(tip)

        menu = QMenu()
        act_clip = menu.addAction("Grab table from clipboard (HTML)")
        act_clip.triggered.connect(self._grab_clipboard_html)
        menu.addSeparator()
        act_about = menu.addAction("About")
        act_about.triggered.connect(self._show_about)
        act_quit = menu.addAction("Quit")
        act_quit.triggered.connect(self._app.quit)
        self._tray.setContextMenu(menu)
        self._tray.show()

        if not self._hook_ok:
            self._tray.showMessage(
                "I hate Grid",
                "Failed to register global mouse hook. Try running as administrator.",
                QSystemTrayIcon.Warning, 5000,
            )

    def _grab_clipboard_html(self) -> None:
        md = QGuiApplication.clipboard().mimeData()
        if md.hasHtml():
            tables = extract_tables(md.html())
            if tables:
                self._show_tables(tables, source="Clipboard HTML")
                return
        self._tray.showMessage(
            "I hate Grid", "No HTML table on the clipboard.",
            QSystemTrayIcon.Information, 3000,
        )

    def _show_about(self) -> None:
        self._about = AboutWindow()
        self._about.show()
        self._about.raise_()
        self._about.activateWindow()

    # ----------------------------------------------------------------
    def _show_tables(self, tables, source: str) -> None:
        w = GridWindow(tables, source=source)
        w.show()
        w.raise_()
        w.activateWindow()
        self._windows.append(w)
        self._prune_windows()

        # Light upkeep: every 3rd grab, drop closed windows + trim RAM.
        self._op_count += 1
        if self._op_count % 3 == 0:
            _trim_memory()

    def _prune_windows(self) -> None:
        alive = []
        for x in self._windows:
            try:
                if x.isVisible():
                    alive.append(x)
                else:
                    x.deleteLater()
            except RuntimeError:
                pass
        self._windows = alive

    def cleanup(self) -> None:
        self._hooks.stop()
        self._hooks.wait(1500)
        self._uia.stop()
        self._uia.wait(1500)


def _make_icon() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(QColor("#2d6cdf"))
    p = QPainter(pm)
    p.setPen(QColor("white"))
    for i in range(1, 4):
        x = i * 16
        p.drawLine(x, 6, x, 58)
        p.drawLine(6, x, 58, x)
    p.end()
    return QIcon(pm)


def main() -> int:
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, "I hate Grid", "System tray is not available.")
        return 1
    controller = App(app)
    app.aboutToQuit.connect(controller.cleanup)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
