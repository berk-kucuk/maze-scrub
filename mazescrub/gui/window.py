"""Main window: a workbench.

Scrub is used on a pile of files at once, so it is laid out like a tool for
that rather than like a page: a toolbar across the top, the list of files on
the left, an inspector on the right that shows everything the selected file
gives away — grouped by what it reveals — and the cleaning controls in a bar
along the bottom.

The list is a model/view list painted by a delegate: thousands of files cost
one row of paint each, not a widget each, and a file that finishes reading
repaints its own row only. All mat2 work runs in the worker pool; the window
never shows a state the engine did not report.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import (QAbstractListModel, QEvent, QModelIndex, QRectF, QSize, QTimer,
                          Qt)
from PyQt6.QtGui import (QColor, QFont, QFontMetrics, QKeySequence, QPainter, QPainterPath,
                         QPen, QShortcut)
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QCheckBox,
                             QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                             QListView, QMainWindow, QMenu, QMessageBox, QPushButton,
                             QScrollArea, QSplitter, QStackedWidget, QStyle,
                             QStyledItemDelegate, QVBoxLayout, QWidget)

from mazescrub import __version__
from mazescrub.core import engine, settings
from mazescrub.core.engine import CleanResult, Report, ScrubError
from mazescrub.gui import worker
from mazescrub.gui.chrome import CopyButton, EdgeResizer, TitleBar, WindowButton, button, label
from mazescrub.gui.glass import GlassBackground, GlassFrame
from mazescrub.gui.glyphs import glyph_pixmap
from mazescrub.gui.i18n import language, set_language, t, upper
from mazescrub.gui.icons import app_logo, logo_pixmap

# While mat2 streams results in, changed rows are repainted at most this often.
_LIVE_MS = 250

_CAT_GLYPH = {"location": "pin", "person": "person", "device": "device",
              "time": "clock", "software": "code"}
FILTERS = ("all", "revealing", "cleaned", "other")


@dataclass
class Item:
    path: Path
    report: Report | None = None
    result: CleanResult | None = None

    @property
    def cleanable(self) -> bool:
        r = self.report
        return bool(r and r.supported and r.fields
                    and not (self.result and self.result.ok))

    def group(self) -> str:
        """Which filter the file belongs to."""
        if self.result and self.result.ok:
            return "cleaned"
        if self.report and self.report.supported and self.report.fields:
            return "revealing"
        return "other"


def shown(text: str) -> str:
    """Text safe to hand to Qt. A file name that is not valid UTF-8 arrives
    as surrogate escapes, which a QString cannot hold."""
    return text.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


def _err_text(e: BaseException) -> str:
    if isinstance(e, ScrubError):
        if e.reason == "cancelled":
            return t("status_stopped")
        if e.reason == "timeout":
            return t("e_timeout")
        return t("e_helper", detail=(e.detail or e.reason)[:200])
    return t("e_helper", detail=str(e)[:200])


def row_state(item: Item) -> tuple[str, str]:
    """(status text, pill style) for a row."""
    r, res = item.report, item.result
    if res is not None:
        if res.ok:
            kept, _ = engine.split_remaining(r.fields if r else [], res.remaining)
            return (t("row_left", n=len(kept)) if kept else t("row_cleaned")), "solid"
        return t("row_failed"), "dashed"
    if r is None:
        return t("row_reading"), "dashed"
    if not r.supported:
        return (t("row_not_read") if r.error == "cancelled" else t("row_unsupported")), "dim"
    if not r.fields:
        return t("row_nothing"), "dim"
    n = len(r.fields)
    return (t("row_one_field") if n == 1 else t("row_fields", n=n)), "outline"


# ── the list ─────────────────────────────────────────────────────────────────

class FileModel(QAbstractListModel):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.keys: list[str] = []

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.keys)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.keys):
            return None
        item = self.win.items.get(self.keys[index.row()])
        if role == Qt.ItemDataRole.UserRole:
            return item
        if role == Qt.ItemDataRole.DisplayRole and item is not None:
            return shown(item.path.name)
        if role == Qt.ItemDataRole.ToolTipRole and item is not None:
            return shown(str(item.path))
        return None

    def rebuild(self, keys: list[str]) -> None:
        self.beginResetModel()
        self.keys = keys
        self.endResetModel()

    def touch(self, keys: set[str]) -> None:
        for row, k in enumerate(self.keys):
            if k in keys:
                idx = self.index(row)
                self.dataChanged.emit(idx, idx)


class RowDelegate(QStyledItemDelegate):
    H = 54

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), self.H)

    def paint(self, p: QPainter, option, index) -> None:
        item: Item | None = index.data(Qt.ItemDataRole.UserRole)
        if item is None:
            return
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(option.rect).adjusted(8, 2, -8, -2)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected or hover:
            path = QPainterPath()
            path.addRoundedRect(r, 10, 10)
            p.fillPath(path, QColor(255, 255, 255, 26 if selected else 10))
            if selected:
                p.setPen(QPen(QColor(255, 255, 255, 50), 1))
                p.drawPath(path)

        text, style = row_state(item)
        bright = item.group() == "revealing"
        icon = glyph_pixmap("file", 18, "#f2f2f4" if bright else "#7d7d83")
        p.drawPixmap(int(r.left() + 12), int(r.center().y() - 9), icon)

        # Pill on the right.
        pf = QFont(option.font)
        pf.setPixelSize(10)
        pf.setBold(True)
        pf.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.6)
        pm = QFontMetrics(pf)
        ptxt = upper(text)
        pw = pm.horizontalAdvance(ptxt) + 18
        pill = QRectF(r.right() - pw - 10, r.center().y() - 10, pw, 20)
        pp = QPainterPath()
        pp.addRoundedRect(pill, 10, 10)
        if style == "solid":
            p.fillPath(pp, QColor("#f2f2f4"))
            fg = QColor("#000000")
        else:
            pen = QPen(QColor(255, 255, 255, 120 if style == "outline" else 50), 1)
            if style == "dashed":
                pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawPath(pp)
            fg = QColor("#e6e6ea" if style == "outline" else "#8a8a90")
        p.setFont(pf)
        p.setPen(fg)
        p.drawText(pill, Qt.AlignmentFlag.AlignCenter, ptxt)

        # Category glyphs left of the pill.
        x = pill.left() - 8
        cats = item.report.categories if item.report else []
        for cat in reversed(cats):
            x -= 16
            p.drawPixmap(int(x), int(r.center().y() - 7),
                         glyph_pixmap(_CAT_GLYPH[cat], 14,
                                      "#e6e6ea" if not (item.result and item.result.ok)
                                      else "#5f5f65"))
        text_right = x - 10

        # Name and folder.
        left = r.left() + 42
        width = max(20, int(text_right - left))
        nf = QFont(option.font)
        nf.setPixelSize(13)
        nf.setBold(True)
        p.setFont(nf)
        p.setPen(QColor("#f2f2f4" if bright or selected else "#c4c4ca"))
        name = QFontMetrics(nf).elidedText(shown(item.path.name),
                                           Qt.TextElideMode.ElideMiddle, width)
        p.drawText(QRectF(left, r.top() + 8, width, 18),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
        sf = QFont(option.font)
        sf.setPixelSize(11)
        p.setFont(sf)
        p.setPen(QColor("#6f6f75"))
        folder = QFontMetrics(sf).elidedText(shown(str(item.path.parent)),
                                             Qt.TextElideMode.ElideMiddle, width)
        p.drawText(QRectF(left, r.top() + 27, width, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, folder)
        p.restore()


class LimitsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("sec_limits"))
        self.setStyleSheet("QDialog { background-color: #0b0b0c; }")
        self.setMinimumWidth(520)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 22, 24, 20)
        v.setSpacing(10)
        v.addWidget(label(t("sec_limits"), "section"))
        for key in ("limit_content", "limit_pdf", "limit_images", "limit_identity"):
            row = QHBoxLayout()
            row.setSpacing(10)
            ic = QLabel()
            ic.setPixmap(glyph_pixmap("point", 14, "#8f8f95"))
            row.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
            row.addWidget(label(t(key), "body", wrap=True), 1)
            v.addLayout(row)
        ok = button("OK", "secondary")
        ok.clicked.connect(self.accept)
        r = QHBoxLayout()
        r.addStretch(1)
        r.addWidget(ok)
        v.addLayout(r)


# ── the window ───────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Maze Scrub")
        self.setWindowIcon(app_logo())
        self.resize(1200, 800)
        self.setMinimumSize(860, 560)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setAcceptDrops(True)
        self._resizer = EdgeResizer(self)
        QApplication.instance().installEventFilter(self._resizer)

        prefs = settings.load()
        self.mode: str = prefs["mode"]
        self.lightweight: bool = prefs["lightweight"]
        self.items: dict[str, Item] = {}
        self.filter = "all"
        self.selected: str | None = None
        self.busy = False
        self.mat2 = engine.Mat2Status(False, reason="checking")
        self._progress = ""
        self._progress_kind = "read"
        self._cancel = threading.Event()
        self._stopped = False        # the user pressed Stop: do not resume on our own
        self._dirty: set[str] = set()
        self._live = QTimer(self, singleShot=True, interval=_LIVE_MS)
        self._live.timeout.connect(self._flush_live)

        self.model = FileModel(self)
        self._build_chrome()
        self.refresh()
        self.check_mat2()
        QShortcut(QKeySequence("Ctrl+Q"), self, activated=self.close)
        QShortcut(QKeySequence("Ctrl+O"), self, activated=self.add_files)
        QShortcut(QKeySequence("Delete"), self, activated=self.remove_selected)

    # ── chrome ─────────────────────────────────────────────────────────────
    def _build_chrome(self) -> None:
        root = GlassBackground()
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        header = TitleBar(self)
        header.setFixedHeight(50)
        h = QHBoxLayout(header)
        h.setContentsMargins(16, 0, 0, 0)
        h.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(logo_pixmap(28))
        h.addWidget(logo)
        h.addWidget(label("MAZE SCRUB", "wordmark"))
        h.addStretch(1)
        for code, text in (("en", "EN"), ("tr", "TR")):
            b = QPushButton(text, objectName="seg_on" if language() == code else "seg")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(t("tip_language"))
            b.clicked.connect(lambda _=False, c=code: self.set_lang(c))
            h.addWidget(b)
        sep = QFrame(objectName="vline")
        sep.setFixedSize(1, 22)
        h.addSpacing(4)
        h.addWidget(sep)
        self._max_btn = WindowButton("win_max", t("tip_maximize"))
        self._max_btn.clicked.connect(
            lambda: self.showNormal() if self.isMaximized() else self.showMaximized())
        mn = WindowButton("win_min", t("tip_minimize"))
        mn.clicked.connect(self.showMinimized)
        cl = WindowButton("win_close", t("tip_close"))
        cl.clicked.connect(self.close)
        for b in (mn, self._max_btn, cl):
            h.addWidget(b)
        v.addWidget(header)

        v.addWidget(self._build_toolbar())

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(1)
        split.addWidget(self._build_list_pane())
        self._inspector = QWidget(objectName="inspector")
        self._inspector.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        il = QVBoxLayout(self._inspector)
        il.setContentsMargins(0, 0, 0, 0)
        self._insp_scroll = QScrollArea()
        self._insp_scroll.setWidgetResizable(True)
        self._insp_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._insp_scroll.viewport().setAutoFillBackground(False)
        self._insp_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        il.addWidget(self._insp_scroll)
        split.addWidget(self._inspector)
        split.setSizes([520, 680])
        v.addWidget(split, 1)

        self._actionbar = QWidget(objectName="actionbar")
        self._actionbar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        v.addWidget(self._actionbar)
        self._build_actionbar()

        footer = QWidget(objectName="footer")
        footer.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        footer.setFixedHeight(30)
        fh = QHBoxLayout(footer)
        fh.setContentsMargins(22, 0, 22, 0)
        self._status = label("", "statusbar")
        fh.addWidget(self._status, 1)
        self._version = label("", "statusbar")
        fh.addWidget(self._version)
        v.addWidget(footer)
        self.setCentralWidget(root)
        self._flash_timer = QTimer(self, singleShot=True, interval=9000)
        self._flash_timer.timeout.connect(self._update_status)

    def _build_toolbar(self) -> QWidget:
        bar = QWidget(objectName="toolbar")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bar.setFixedHeight(56)
        h = QHBoxLayout(bar)
        h.setContentsMargins(16, 0, 16, 0)
        h.setSpacing(8)
        self._add_btn = button(t("btn_add_files"), "get", "add_files")
        self._add_btn.clicked.connect(self.add_files)
        self._folder_btn = button(t("btn_add_folder"), "secondary", "folder")
        self._folder_btn.clicked.connect(self.add_folder)
        self._stop_btn = button(t("btn_stop"), "secondary", "x")
        self._stop_btn.clicked.connect(self.stop)
        for b in (self._add_btn, self._folder_btn, self._stop_btn):
            h.addWidget(b)
        sep = QFrame(objectName="vline")
        sep.setFixedSize(1, 22)
        h.addSpacing(8)
        h.addWidget(sep)
        h.addSpacing(8)
        self._filter_btns: dict[str, QPushButton] = {}
        for f in FILTERS:
            b = QPushButton(objectName="seg")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, x=f: self.set_filter(x))
            self._filter_btns[f] = b
            h.addWidget(b)
        h.addStretch(1)
        lim = button(t("btn_limits"), "ghost", "warn")
        lim.clicked.connect(lambda: LimitsDialog(self).exec())
        h.addWidget(lim)
        self._clear_btn = button(t("btn_clear"), "ghost", "trash")
        self._clear_btn.clicked.connect(self.clear)
        h.addWidget(self._clear_btn)
        return bar

    def _build_list_pane(self) -> QWidget:
        self._list_stack = QStackedWidget()
        # Page 0: the empty state, which is also the welcome.
        empty = QWidget()
        ev = QVBoxLayout(empty)
        ev.setContentsMargins(36, 30, 36, 30)
        ev.addStretch(1)
        ic = QLabel()
        ic.setPixmap(glyph_pixmap("drop", 46, "#8f8f95"))
        ev.addWidget(ic, 0, Qt.AlignmentFlag.AlignHCenter)
        ev.addSpacing(8)
        title = label(t("list_empty_title"), "empty_title", wrap=True)
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        ev.addWidget(title)
        sub = label(t("list_empty_sub"), "tagline", wrap=True)
        sub.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        ev.addWidget(sub)
        ev.addSpacing(6)
        hint = label(t("drop_hint"), "drop", wrap=True)
        hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        ev.addWidget(hint)
        ev.addStretch(1)
        self._list_stack.addWidget(empty)
        # Page 1: the list.
        self._list = QListView(objectName="filelist")
        self._list.setModel(self.model)
        self._list.setItemDelegate(RowDelegate(self._list))
        self._list.setUniformItemSizes(True)
        self._list.setMouseTracking(True)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._context_menu)
        self._list.selectionModel().currentChanged.connect(self._on_current)
        self._list_stack.addWidget(self._list)
        return self._list_stack

    def _build_actionbar(self) -> None:
        old = self._actionbar.layout()
        if old is not None:
            QWidget().setLayout(old)          # detach and drop the old layout
        h = QHBoxLayout(self._actionbar)
        h.setContentsMargins(20, 12, 20, 12)
        h.setSpacing(14)
        seg = QHBoxLayout()
        seg.setSpacing(4)
        self._mode_btns = {}
        for mode, key in (("copy", "mode_copy_short"), ("replace", "mode_replace_short")):
            b = QPushButton(t(key), objectName="seg_on" if self.mode == mode else "seg")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, m=mode: self.set_mode(m))
            self._mode_btns[mode] = b
            seg.addWidget(b)
        h.addLayout(seg)
        self._mode_hint = label(t("mode_copy_hint" if self.mode == "copy" else
                                  "mode_replace_hint"), "hint", wrap=True)
        h.addWidget(self._mode_hint, 1)
        self._light = QCheckBox(t("lightweight"))
        self._light.setChecked(self.lightweight)
        self._light.setToolTip(t("lightweight_hint"))
        self._light.toggled.connect(self.set_lightweight)
        h.addWidget(self._light)
        self._clean_btn = button(t("btn_clean_none"), "get_lg", "scrub", icon_color="#000000")
        self._clean_btn.clicked.connect(self.clean)
        h.addWidget(self._clean_btn)

    def changeEvent(self, ev) -> None:
        if ev.type() == QEvent.Type.WindowStateChange:
            self._max_btn.set_kind("win_restore" if self.isMaximized() else "win_max")
        super().changeEvent(ev)

    def set_lang(self, code: str) -> None:
        if code == language():
            return
        set_language(code)
        settings.save(language=code)
        # Deferred: this runs inside the clicked() of a button in the header
        # being replaced, and deleting a widget inside its own signal crashes.
        QTimer.singleShot(0, self._rebuild)

    def _rebuild(self) -> None:
        self._build_chrome()
        self.refresh()

    def closeEvent(self, ev) -> None:
        # Stop the helpers now; the app waits for the pool before exiting, and
        # a killed helper ends its stream within a quarter of a second.
        self._cancel.set()
        super().closeEvent(ev)

    def stop(self) -> None:
        if self.busy:
            self._stopped = True
            self._cancel.set()

    # ── status ─────────────────────────────────────────────────────────────
    def flash(self, text: str) -> None:
        self._status.setText(text)
        self._status.setToolTip(text)
        self._flash_timer.start()

    def _update_status(self) -> None:
        if self._progress:
            self._status.setText(self._progress)
        elif self.items:
            self._status.setText(t("status_ready", n=len(self.items)))
        else:
            self._status.setText(t("status_empty"))
        mat2 = f"mat2 {self.mat2.version}  ·  " if self.mat2.version else ""
        self._version.setText(f"{mat2}Maze Scrub {__version__}")

    # ── mat2 ───────────────────────────────────────────────────────────────
    def check_mat2(self) -> None:
        def done(st: engine.Mat2Status) -> None:
            self.mat2 = st
            self.refresh()
            self._inspect_pending()
        worker.run(engine.status, on_done=done,
                   on_fail=lambda e: done(engine.Mat2Status(False, reason="helper")),
                   critical=False)

    # ── adding ─────────────────────────────────────────────────────────────
    def add_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, t("btn_add_files"), str(Path.home()))
        if files:
            self.add_paths(files)

    def add_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, t("btn_add_folder"), str(Path.home()))
        if d:
            self.add_paths([d])

    def add_paths(self, paths: list[str]) -> None:
        if not paths:
            return

        def done(res: tuple[list[Path], bool]) -> None:
            files, truncated = res
            for f in files:
                item = self.items.setdefault(str(f), Item(f))
                if item.report is not None and item.report.error == "cancelled":
                    item.report = None          # added again after a Stop: read it now
            self._stopped = False
            self.refresh()
            if truncated:
                self.flash(t("status_truncated", n=engine.MAX_FILES))
            self._inspect_pending()
        worker.run(engine.expand, list(paths), on_done=done,
                   on_fail=lambda e: self.flash(_err_text(e)), critical=False)

    def _inspect_pending(self) -> None:
        if self.busy or not self.mat2.available:
            return
        pending = [i.path for i in self.items.values() if i.report is None]
        if not pending:
            return
        self.busy = True
        self._cancel = threading.Event()
        cancel = self._cancel

        def done(reports: list[Report]) -> None:
            for r in reports:
                item = self.items.get(str(r.path))
                if item is not None and item.report is None:
                    item.report = r
            self._finish()

        def fail(e: BaseException) -> None:
            if isinstance(e, ScrubError) and e.reason == "cancelled":
                for item in self.items.values():
                    if item.report is None:
                        item.report = Report(item.path, error="cancelled")
            self._finish()
            self.flash(_err_text(e))

        def job(progress) -> list[Report]:
            return engine.inspect(pending, cancel=cancel,
                                  on_item=lambda r: progress("item", r),
                                  progress=lambda d, n: progress("count", d, n))
        worker.run(job, on_done=done, on_fail=fail, on_progress=self._on_progress,
                   with_progress=True, critical=False)
        self._update_controls()

    def _on_progress(self, v: tuple) -> None:
        """One file finished (in the pool) — show it without waiting for the rest."""
        if not v:
            return
        if v[0] == "item":
            obj = v[1]
            key = str(obj.path)
            item = self.items.get(key)
            if item is not None:
                if isinstance(obj, Report):
                    item.report = obj
                else:
                    item.result = obj
                self._dirty.add(key)
                if not self._live.isActive():
                    self._live.start()
        elif v[0] == "count":
            _kind, done_n, total, *_ = v
            key = "cleaning" if self._progress_kind == "clean" else "reading"
            self._progress = t(key, done=done_n, total=total)
            self._update_status()

    def _flush_live(self) -> None:
        """Repaint the rows that changed; leave the rest of the window alone."""
        dirty, self._dirty = self._dirty, set()
        if self.filter != "all":
            self.refresh()               # a row may now belong to another filter
            return
        self.model.touch(dirty)
        if self.selected in dirty:
            self._render_inspector()
        self._update_controls()

    def _finish(self) -> None:
        self.busy = False
        self._progress = ""
        self.refresh()
        # Files dropped while reading wait for the next pass — unless the user
        # stopped, or the window is closing.
        if (not self._stopped and not self._cancel.is_set()
                and any(i.report is None for i in self.items.values())):
            QTimer.singleShot(0, self._inspect_pending)

    # ── list actions ───────────────────────────────────────────────────────
    def select(self, item: Item) -> None:
        key = str(item.path)
        if key in self.model.keys:
            self._list.setCurrentIndex(self.model.index(self.model.keys.index(key)))
        self.selected = key
        self._render_inspector()

    def _on_current(self, current: QModelIndex, _prev: QModelIndex) -> None:
        self.selected = self.model.keys[current.row()] if current.isValid() else None
        self._render_inspector()

    def _context_menu(self, pos) -> None:
        idx = self._list.indexAt(pos)
        if not idx.isValid() or self.busy:
            return
        m = QMenu(self)
        a = m.addAction(t("menu_remove"))
        key = self.model.keys[idx.row()]
        a.triggered.connect(lambda: self.remove_key(key))
        m.exec(self._list.viewport().mapToGlobal(pos))

    def remove_selected(self) -> None:
        if self.selected and not self.busy:
            self.remove_key(self.selected)

    def remove_key(self, key: str) -> None:
        if not self.busy:
            self.items.pop(key, None)
            if self.selected == key:
                self.selected = None
            self.refresh()

    def remove(self, item: Item) -> None:
        self.remove_key(str(item.path))

    def clear(self) -> None:
        if not self.busy:
            self.items.clear()
            self.selected = None
            self.refresh()

    def set_filter(self, f: str) -> None:
        self.filter = f
        self.refresh()

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        settings.save(mode=mode)
        for m, b in self._mode_btns.items():
            b.setObjectName("seg_on" if m == mode else "seg")
            b.style().unpolish(b)
            b.style().polish(b)
        self._mode_hint.setText(t("mode_copy_hint" if mode == "copy" else "mode_replace_hint"))

    def set_lightweight(self, on: bool) -> None:
        self.lightweight = on
        settings.save(lightweight=on)

    # ── cleaning ───────────────────────────────────────────────────────────
    def clean(self) -> None:
        targets = [i for i in self.items.values() if i.cleanable]
        if self.busy or not targets:
            return
        if self.mode == "replace":
            box = QMessageBox(self)
            box.setWindowTitle(t("confirm_replace_title"))
            box.setText(t("confirm_replace_body", n=len(targets)))
            yes = box.addButton(t("btn_replace"), QMessageBox.ButtonRole.AcceptRole)
            box.addButton(t("btn_cancel"), QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is not yes:
                return
        self.busy = True
        self._progress_kind = "clean"
        self._cancel = threading.Event()
        cancel = self._cancel
        paths = [i.path for i in targets]
        mode, light = self.mode, self.lightweight

        def done(results: list[CleanResult]) -> None:
            for res in results:
                item = self.items.get(str(res.path))
                if item is not None:
                    item.result = res
            self._progress_kind = "read"
            self._finish()
            ok = sum(1 for r in results if r.ok)
            self.flash(t("status_done", ok=ok, failed=len(results) - ok))

        def fail(e: BaseException) -> None:
            self._progress_kind = "read"
            self._finish()
            self.flash(_err_text(e))

        def job(progress) -> list[CleanResult]:
            return engine.clean(paths, mode=mode, lightweight=light, cancel=cancel,
                                on_item=lambda r: progress("item", r),
                                progress=lambda d, n: progress("count", d, n))
        worker.run(job, on_done=done, on_fail=fail, on_progress=self._on_progress,
                   with_progress=True)
        self._update_controls()

    # ── drag & drop ────────────────────────────────────────────────────────
    def dragEnterEvent(self, ev) -> None:
        md = ev.mimeData()
        if md.hasUrls() and any(u.isLocalFile() for u in md.urls()):
            ev.acceptProposedAction()

    def dropEvent(self, ev) -> None:
        paths = [u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()]
        if paths:
            ev.acceptProposedAction()
            QTimer.singleShot(0, lambda: self.add_paths(paths))

    # ── refresh ────────────────────────────────────────────────────────────
    def visible_keys(self) -> list[str]:
        if self.filter == "all":
            return list(self.items)
        return [k for k, i in self.items.items() if i.group() == self.filter]

    def refresh(self) -> None:
        """Rebuild the list and everything that depends on it. Cheap: the list
        is a model, so this costs nothing per row until rows are painted."""
        keys = self.visible_keys()
        keep = self.selected
        self._dirty.clear()              # the rebuild repaints every row anyway
        self.model.rebuild(keys)
        self._list_stack.setCurrentIndex(1 if self.items else 0)
        if keep in keys:
            self._list.setCurrentIndex(self.model.index(keys.index(keep)))
        elif keys and self.selected is None:
            self.selected = keys[0]
            self._list.setCurrentIndex(self.model.index(0))
        self._render_inspector()
        self._update_controls()

    def _update_controls(self) -> None:
        counts = {f: 0 for f in FILTERS}
        for i in self.items.values():
            counts["all"] += 1
            counts[i.group()] += 1
        names = {"all": "filter_all", "revealing": "filter_revealing",
                 "cleaned": "filter_cleaned", "other": "filter_other"}
        for f, b in self._filter_btns.items():
            b.setText(f"{t(names[f])}  {counts[f]}")
            b.setObjectName("seg_on" if self.filter == f else "seg")
            b.style().unpolish(b)
            b.style().polish(b)
            b.setVisible(bool(self.items))
        self._stop_btn.setVisible(self.busy)
        self._stop_btn.setEnabled(not self._stopped)
        self._clear_btn.setEnabled(bool(self.items) and not self.busy)
        n = sum(1 for i in self.items.values() if i.cleanable)
        self._clean_btn.setText(t("btn_clean_none") if n == 0 else
                                t("btn_clean_one") if n == 1 else t("btn_clean", n=n))
        self._clean_btn.setEnabled(n > 0 and not self.busy and self.mat2.available)
        for b in self._mode_btns.values():
            b.setEnabled(not self.busy)
        self._light.setEnabled(not self.busy)
        self._add_btn.setEnabled(self.mat2.available or self.mat2.reason == "checking")
        self._folder_btn.setEnabled(self._add_btn.isEnabled())
        self._update_status()

    # ── inspector ──────────────────────────────────────────────────────────
    def _render_inspector(self) -> None:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(28, 24, 28, 24)
        v.setSpacing(10)
        if not self.mat2.available and self.mat2.reason != "checking":
            v.addWidget(self._no_mat2())
        elif self.mat2.missing:
            v.addWidget(label(t("missing_tools", names=", ".join(self.mat2.missing)),
                              "hint", wrap=True))
        item = self.items.get(self.selected or "")
        if item is None:
            if self.items:
                v.addStretch(1)
                msg = label(t("inspector_empty"), "tagline", wrap=True)
                msg.setAlignment(Qt.AlignmentFlag.AlignHCenter)
                v.addWidget(msg)
            v.addStretch(1)
        else:
            self._inspect_item(v, item)
            v.addStretch(1)
        old = self._insp_scroll.takeWidget()
        self._insp_scroll.setWidget(page)
        if old is not None:
            old.deleteLater()

    def _inspect_item(self, v: QVBoxLayout, item: Item) -> None:
        name = label(shown(item.path.name), "insp_name", wrap=True)
        v.addWidget(name)
        where = label(shown(str(item.path.parent)), "path", wrap=True)
        v.addWidget(where)
        r, res = item.report, item.result
        if r and r.categories:
            v.addSpacing(4)
            cats = ", ".join(t(f"cat_{c}") for c in r.categories)
            v.addWidget(label(t("reveals", cats=cats),
                              "meta" if res and res.ok else "warnline", wrap=True))
        v.addSpacing(8)

        if r is None:
            v.addWidget(label(t("row_reading"), "tagline"))
            return
        if not r.supported:
            text = (t("not_read") if r.error == "cancelled" else
                    t("error_body", detail=shown(r.error)) if r.error else t("unsupported_body"))
            v.addWidget(label(text, "tagline", wrap=True))
            return
        if not r.fields:
            v.addWidget(label(t("inspector_none"), "tagline", wrap=True))
            return

        if res is not None:
            v.addWidget(self._result_box(item))
            v.addSpacing(6)
            if res.ok:
                v.addWidget(label(t("found_before"), "field"))

        groups: dict[str, list] = {c: [] for c in (*engine.CATEGORIES, "")}
        for f in r.fields:
            groups[f.category].append(f)
        for cat in (*engine.CATEGORIES, ""):
            fields = groups[cat]
            if not fields:
                continue
            head = QHBoxLayout()
            head.setSpacing(8)
            if cat:
                ic = QLabel()
                ic.setPixmap(glyph_pixmap(_CAT_GLYPH[cat], 15, "#e6e6ea"))
                head.addWidget(ic)
                head.addWidget(label(upper(t(f"cat_{cat}")), "cat_head"))
            else:
                head.addWidget(label(t("sec_other"), "section"))
            head.addWidget(label(str(len(fields)), "meta"))
            head.addStretch(1)
            v.addSpacing(8)
            v.addLayout(head)
            grid = QGridLayout()
            grid.setHorizontalSpacing(18)
            grid.setVerticalSpacing(4)
            grid.setContentsMargins(23 if cat else 0, 2, 0, 0)
            for row, f in enumerate(fields[:400]):
                k = label(shown(f.key), "field_key" if cat else "field_key_dim")
                # One key column width for every group, so values line up
                # down the whole inspector, not just within one category.
                k.setFixedWidth(180)
                k.setText(k.fontMetrics().elidedText(shown(f.key), Qt.TextElideMode.ElideMiddle, 176))
                k.setToolTip(shown(f.key))
                val = label(shown(f.value), "field_val" if cat else "field_val_dim", wrap=True)
                val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                grid.addWidget(k, row, 0, Qt.AlignmentFlag.AlignTop)
                grid.addWidget(val, row, 1)
            grid.setColumnStretch(1, 1)
            v.addLayout(grid)

    def _result_box(self, item: Item) -> QWidget:
        res = item.result
        box = GlassFrame(radius=14, strength=1.3 if res.ok else 1.0)
        v = QVBoxLayout(box)
        v.setContentsMargins(16, 12, 16, 12)
        v.setSpacing(4)
        v.addWidget(label(t("sec_result"), "section"))
        name = shown(res.output.name) if res.output else ""
        if res.ok and res.replaced:
            v.addWidget(label(t("res_replaced"), "body", wrap=True))
        elif res.ok and not res.error:
            v.addWidget(label(t("res_copy", name=name), "body", wrap=True))
        if res.error:
            v.addWidget(label(t(f"e_{res.error}", name=name), "warnline", wrap=True))
        if res.ok:
            original = item.report.fields if item.report else []
            kept, rewritten = engine.split_remaining(original, res.remaining)

            def keys(fields) -> str:
                text = ", ".join(shown(f.key) for f in fields[:8])
                return text + (", …" if len(fields) > 8 else "")
            if kept:
                v.addWidget(label(t("res_left", keys=keys(kept)), "warnline", wrap=True))
            if rewritten:
                v.addWidget(label(t("res_rewritten", keys=keys(rewritten)), "hint", wrap=True))
            if not res.remaining:
                v.addWidget(label(t("res_none_left"), "hint", wrap=True))
        return box

    def _no_mat2(self) -> QWidget:
        box = GlassFrame(radius=16, strength=1.3)
        v = QVBoxLayout(box)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(10)
        head = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(glyph_pixmap("warn", 18, "#f2f2f4"))
        head.addWidget(ic)
        head.addWidget(label(t("no_mat2_title"), "banner_title"), 1)
        v.addLayout(head)
        if self.mat2.reason == "no_python":
            v.addWidget(label(t("no_python_body"), "body", wrap=True))
        else:
            v.addWidget(label(t("no_mat2_body"), "body", wrap=True))
            cmd = "sudo pacman -S mat2"
            row = QHBoxLayout()
            row.addWidget(label(cmd, "command"), 1)
            row.addWidget(CopyButton("", cmd))
            v.addLayout(row)
        again = button(t("btn_recheck"), "secondary", "restore")
        again.clicked.connect(self.check_mat2)
        r = QHBoxLayout()
        r.addWidget(again)
        r.addStretch(1)
        v.addLayout(r)
        return box
