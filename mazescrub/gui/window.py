"""Main window: add files, see what they reveal, clean them.

Frameless with its own 50px title bar, like the rest of the Maze apps. The page
is one column: the hero with the add buttons, the file list (each file with
its fields grouped by what they give away), the action bar, and a plain list
of what Scrub cannot do — said up front rather than discovered later.

All mat2 work runs in the worker pool; the page is rebuilt from `self.items`
after every step, so the window never shows a state the engine did not report.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QEvent, QSize, QTimer, Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QFileDialog, QFrame,
                             QGridLayout, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
                             QPushButton, QRadioButton, QScrollArea, QVBoxLayout, QWidget)

from mazescrub import __version__
from mazescrub.core import engine, settings
from mazescrub.core.engine import CleanResult, Report, ScrubError
from mazescrub.gui import worker
from mazescrub.gui.chrome import CopyButton, EdgeResizer, TitleBar, WindowButton, button, label
from mazescrub.gui.glass import GlassBackground, GlassFrame
from mazescrub.gui.glyphs import glyph, glyph_pixmap
from mazescrub.gui.i18n import language, set_language, t
from mazescrub.gui.icons import app_logo, logo_pixmap
from mazescrub.gui.pill import Pill

_CONTENT_W = 980
_MAX_ROWS = 200          # rows drawn; the rest are summarised in one line
# While mat2 streams results in, the page is rebuilt at most this often (ms):
# rebuilding hundreds of rows for every file would starve the event loop.
_LIVE_RENDER_MS = 1000

_CAT_GLYPH = {"location": "pin", "person": "person", "device": "device",
              "time": "clock", "software": "code"}


@dataclass
class Item:
    path: Path
    report: Report | None = None
    result: CleanResult | None = None
    expanded: bool = False

    @property
    def cleanable(self) -> bool:
        r = self.report
        return bool(r and r.supported and r.fields
                    and not (self.result and self.result.ok))


def shown(text: str) -> str:
    """Text safe to hand to Qt. A file name that is not valid UTF-8 arrives
    as surrogate escapes, which a QString cannot hold."""
    return text.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


def _elide(lb: QLabel, text: str, width: int) -> None:
    text = shown(text)
    lb.setText(lb.fontMetrics().elidedText(text, Qt.TextElideMode.ElideMiddle, width))
    lb.setToolTip(text)


def _err_text(e: BaseException) -> str:
    if isinstance(e, ScrubError):
        if e.reason == "cancelled":
            return t("status_stopped")
        if e.reason == "timeout":
            return t("e_timeout")
        return t("e_helper", detail=(e.detail or e.reason)[:200])
    return t("e_helper", detail=str(e)[:200])


class FileRow(GlassFrame):
    def __init__(self, item: Item, win: "MainWindow"):
        super().__init__(radius=14, strength=1.1 if item.expanded else 1.0)
        self.item, self.win = item, win
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 12, 12, 12)
        v.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(10)
        ic = QLabel()
        ic.setPixmap(glyph_pixmap("file", 18, "#bdbdc2"))
        top.addWidget(ic)
        names = QVBoxLayout()
        names.setSpacing(1)
        names.addWidget(label(shown(item.path.name), "title"))
        where = label("", "path")
        _elide(where, str(item.path.parent), 420)
        names.addWidget(where)
        top.addLayout(names, 1)

        r, res = item.report, item.result
        if r and r.categories:
            cats = ", ".join(t(f"cat_{c}") for c in r.categories)
            names.addWidget(label(t("reveals", cats=cats),
                                  "meta" if res and res.ok else "warnline"))
        for cat in (r.categories if r else []):
            chip = QLabel()
            chip.setPixmap(glyph_pixmap(_CAT_GLYPH[cat], 15, "#e6e6ea"))
            chip.setToolTip(t(f"cat_{cat}"))
            top.addWidget(chip)
        top.addSpacing(4)
        top.addWidget(self._pill())

        if r and r.supported and r.fields:
            more = button(t("btn_hide") if item.expanded else t("btn_details"), "ghost")
            more.clicked.connect(lambda: win.toggle(item))
            top.addWidget(more)
        rm = QPushButton(objectName="round")
        rm.setIcon(glyph("x", 14, "#9a9aa0"))
        rm.setIconSize(QSize(14, 14))
        rm.setFixedSize(32, 32)
        rm.setCursor(Qt.CursorShape.PointingHandCursor)
        rm.setToolTip(t("tip_remove"))
        rm.setEnabled(not win.busy)
        rm.clicked.connect(lambda: win.remove(item))
        self._rm = rm
        top.addWidget(rm)
        v.addLayout(top)

        if r and not r.supported:
            # The helper leaves `error` empty for "mat2 has no parser for this";
            # anything else is a file it tried and failed to read.
            text = (t("not_read") if r.error == "cancelled" else
                    t("error_body", detail=shown(r.error)) if r.error else t("unsupported_body"))
            v.addWidget(self._indent(label(text, "hint", wrap=True)))
        if item.expanded and r and r.fields:
            if res and res.ok:
                v.addWidget(self._indent(label(t("found_before"), "field")))
            v.addWidget(self._indent(self._fields(r)))
        if res:
            v.addWidget(self._indent(self._result(res)))

    @staticmethod
    def signature(item: Item) -> tuple:
        """Everything a row draws, apart from the busy state. Same signature,
        same pixels — so the row widget can be kept across renders."""
        return (id(item.report), id(item.result), item.expanded, language())

    def set_busy(self, busy: bool) -> None:
        self._rm.setEnabled(not busy)

    def _indent(self, w: QWidget) -> QWidget:
        box = QWidget()
        h = QHBoxLayout(box)
        h.setContentsMargins(28, 0, 8, 0)
        h.addWidget(w, 1)
        return box

    def _pill(self) -> Pill:
        r, res = self.item.report, self.item.result
        if res:
            return Pill(t("pill_cleaned"), "update") if res.ok else Pill(t("pill_failed"), "busy")
        if r is None:
            return Pill(t("pill_scanning"), "busy")
        if not r.supported:
            if r.error == "cancelled":
                return Pill(t("pill_stopped"), "local")
            return Pill(t("pill_error") if r.error else t("pill_unsupported"), "local")
        if not r.fields:
            return Pill(t("pill_none"), "installed")
        n = len(r.fields)
        return Pill(t("pill_one_field") if n == 1 else t("pill_fields", n=n), "chip")

    def _fields(self, r: Report) -> QWidget:
        box = QWidget()
        g = QGridLayout(box)
        g.setContentsMargins(0, 4, 0, 4)
        g.setHorizontalSpacing(16)
        g.setVerticalSpacing(3)
        # What gives the user away first, then the rest.
        order = {c: i for i, c in enumerate(engine.CATEGORIES)}
        fields = sorted(r.fields, key=lambda f: order.get(f.category, 99))
        for row, f in enumerate(fields[:400]):
            k = label(shown(f.key), "mono")
            val = label(shown(f.value), "body" if f.category else "mono", wrap=True)
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            if f.category:
                k.setStyleSheet("color: #f2f2f4; font-weight: 600;")
                k.setToolTip(t(f"cat_{f.category}"))
            g.addWidget(k, row, 0, Qt.AlignmentFlag.AlignTop)
            g.addWidget(val, row, 1)
        g.setColumnStretch(1, 1)
        return box

    def _result(self, res: CleanResult) -> QWidget:
        lines: list[tuple[str, str]] = []
        name = shown(res.output.name) if res.output else ""
        if res.ok and res.replaced:
            lines.append((t("res_replaced"), "meta"))
        elif res.ok and not res.error:
            lines.append((t("res_copy", name=name), "meta"))
        if res.error:
            lines.append((t(f"e_{res.error}", name=name), "warnline"))
        if res.ok:
            original = self.item.report.fields if self.item.report else []
            kept, rewritten = engine.split_remaining(original, res.remaining)

            def keys(fields) -> str:
                text = ", ".join(shown(f.key) for f in fields[:8])
                return text + (", …" if len(fields) > 8 else "")
            if kept:
                lines.append((t("res_left", keys=keys(kept)), "warnline"))
            if rewritten:
                lines.append((t("res_rewritten", keys=keys(rewritten)), "hint"))
            if not res.remaining:
                lines.append((t("res_none_left"), "hint"))
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        for text, style in lines:
            lay.addWidget(label(text, style, wrap=True))
        return box


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Maze Scrub")
        self.setWindowIcon(app_logo())
        self.resize(1080, 860)
        self.setMinimumSize(720, 560)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setAcceptDrops(True)
        self._resizer = EdgeResizer(self)
        QApplication.instance().installEventFilter(self._resizer)

        prefs = settings.load()
        self.mode: str = prefs["mode"]
        self.lightweight: bool = prefs["lightweight"]
        self.items: dict[str, Item] = {}
        self.busy = False
        self.mat2 = engine.Mat2Status(False, reason="checking")
        self._progress = ""
        self._progress_kind = "read"
        self._cancel = threading.Event()
        self._stopped = False        # the user pressed Stop: do not resume on our own
        self._render_timer = QTimer(self, singleShot=True)
        self._render_timer.timeout.connect(self._render)

        self._rows: dict[str, tuple[tuple, FileRow]] = {}
        self._sections: dict[str, QWidget] = {}
        self._build_chrome()
        self._render()
        self.check_mat2()
        QShortcut(QKeySequence("Ctrl+Q"), self, activated=self.close)
        QShortcut(QKeySequence("Ctrl+O"), self, activated=self.add_files)

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

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.viewport().setAutoFillBackground(False)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        v.addWidget(self._scroll, 1)

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
        self._rows.clear()
        self._sections.clear()
        self._build_chrome()
        self._render()

    def schedule_render(self, delay: int = 0) -> None:
        """Rebuild the page from the event loop, never from inside a slot of a
        widget that the rebuild deletes. Calls inside `delay` coalesce."""
        if delay == 0:
            self._render_timer.start(0)
        elif not self._render_timer.isActive():
            self._render_timer.start(delay)

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
            self.schedule_render()
            self._inspect_pending()
        worker.run(engine.status, on_done=done,
                   on_fail=lambda e: done(engine.Mat2Status(False, reason="helper")),
                   critical=False)

    # ── adding ─────────────────────────────────────────────────────────────
    def add_files(self) -> None:
        if self.busy:
            return
        files, _ = QFileDialog.getOpenFileNames(self, t("btn_add_files"), str(Path.home()))
        if files:
            self.add_paths(files)

    def add_folder(self) -> None:
        if self.busy:
            return
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
            self.schedule_render()
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
        self.schedule_render()

    def _on_progress(self, v: tuple) -> None:
        """One file finished (in the pool) — show it without waiting for the rest."""
        if not v:
            return
        if v[0] == "item":
            obj = v[1]
            item = self.items.get(str(obj.path))
            if item is not None:
                if isinstance(obj, Report):
                    item.report = obj
                else:
                    item.result = obj
            self.schedule_render(_LIVE_RENDER_MS)
        elif v[0] == "count":
            _kind, done_n, total, *_ = v
            key = "cleaning" if self._progress_kind == "clean" else "reading"
            self._progress = t(key, done=done_n, total=total)
            self._update_status()

    def _finish(self) -> None:
        self.busy = False
        self._progress = ""
        self.schedule_render()
        # Files dropped while reading wait for the next pass — unless the user
        # stopped, or the window is closing.
        if (not self._stopped and not self._cancel.is_set()
                and any(i.report is None for i in self.items.values())):
            QTimer.singleShot(0, self._inspect_pending)

    # ── list actions ───────────────────────────────────────────────────────
    def toggle(self, item: Item) -> None:
        item.expanded = not item.expanded
        self.schedule_render()

    def remove(self, item: Item) -> None:
        if not self.busy:
            self.items.pop(str(item.path), None)
            self.schedule_render()

    def clear(self) -> None:
        if not self.busy:
            self.items.clear()
            self.schedule_render()

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        settings.save(mode=mode)

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
        self.schedule_render()

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

    # ── page ───────────────────────────────────────────────────────────────
    def _build_page(self) -> None:
        """The persistent page: one column of named slots. Renders swap the
        small sections and reuse file rows, instead of rebuilding hundreds
        of widgets every second while a big folder streams in."""
        page = QWidget()
        outer = QHBoxLayout(page)
        outer.setContentsMargins(24, 24, 24, 28)
        outer.addStretch(1)
        col = QWidget()
        col.setMaximumWidth(_CONTENT_W)
        self._col = QVBoxLayout(col)
        self._col.setContentsMargins(0, 0, 0, 0)
        self._col.setSpacing(14)
        outer.addWidget(col, 100)
        outer.addStretch(1)
        for name in ("hero", "notice", "header", "empty"):
            self._sections[name] = QWidget()
            self._sections[name].hide()
            self._col.addWidget(self._sections[name])
        rows = QWidget()
        self._rows_layout = QVBoxLayout(rows)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(10)
        self._col.addWidget(rows)
        for name in ("more", "actions", "limits"):
            self._sections[name] = QWidget()
            self._sections[name].hide()
            self._col.addWidget(self._sections[name])
        self._col.addStretch(1)
        self._scroll.setWidget(page)

    def _swap(self, name: str, widget: QWidget | None) -> None:
        """Put `widget` in slot `name`; None leaves the slot empty and hidden,
        so it adds no spacing."""
        empty = widget is None
        widget = widget or QWidget()
        old = self._sections[name]
        self._col.replaceWidget(old, widget)
        old.hide()
        old.deleteLater()            # never delete synchronously: see schedule_render
        self._sections[name] = widget
        widget.setVisible(not empty)

    def _render(self) -> None:
        self._render_timer.stop()
        if not self._sections:
            self._build_page()

        self._swap("hero", self._hero())
        notice = None
        if not self.mat2.available and self.mat2.reason != "checking":
            notice = self._no_mat2()
        elif self.mat2.missing:
            notice = label(t("missing_tools", names=", ".join(self.mat2.missing)),
                           "hint", wrap=True)
        self._swap("notice", notice)
        header = QWidget()
        header.setLayout(self._files_header())
        self._swap("header", header)
        self._swap("empty", None if self.items else self._empty())
        self._render_rows()
        extra = len(self.items) - _MAX_ROWS
        self._swap("more", label(f"+ {extra}", "meta") if extra > 0 else None)
        self._swap("actions", self._action_bar() if self.items else None)
        if self._sections["limits"].layout() is None:
            self._swap("limits", self._limits())
        self._update_status()

    def _render_rows(self) -> None:
        wanted = list(self.items.values())[:_MAX_ROWS]
        keep: dict[str, tuple[tuple, FileRow]] = {}
        for item in wanted:
            key = str(item.path)
            sig = FileRow.signature(item)
            cached = self._rows.get(key)
            if cached and cached[0] == sig:
                row = cached[1]
                row.set_busy(self.busy)
            else:
                row = FileRow(item, self)
            keep[key] = (sig, row)
        for key, (_sig, row) in self._rows.items():
            if keep.get(key, (None, None))[1] is not row:
                row.hide()
                row.deleteLater()
        # Re-seat in list order; taking a widget out of a layout is cheap.
        while self._rows_layout.count():
            self._rows_layout.takeAt(0)
        for _sig, row in keep.values():
            self._rows_layout.addWidget(row)
            row.show()
        self._rows = keep

    def _hero(self) -> QWidget:
        hero = GlassFrame(radius=22, tint="#ffffff", strength=1.2)
        v = QVBoxLayout(hero)
        v.setContentsMargins(32, 28, 32, 28)
        v.setSpacing(10)
        v.addWidget(label(t("eyebrow"), "eyebrow"))
        v.addWidget(label(t("hero_title"), "hero_title", wrap=True))
        v.addWidget(label(t("hero_sub"), "hero_sub", wrap=True))
        v.addSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(10)
        add = button(t("btn_add_files"), "get_lg", "add_files")
        add.clicked.connect(self.add_files)
        fold = button(t("btn_add_folder"), "secondary_lg", "folder")
        fold.clicked.connect(self.add_folder)
        for b in (add, fold):
            b.setEnabled(not self.busy)
            row.addWidget(b)
        di = QLabel()
        di.setPixmap(glyph_pixmap("drop", 14, "#6f6f75"))
        row.addSpacing(8)
        row.addWidget(di)
        row.addWidget(label(t("drop_hint"), "drop"), 1)
        v.addLayout(row)
        return hero

    def _no_mat2(self) -> QWidget:
        box = GlassFrame(radius=16, strength=1.3)
        v = QVBoxLayout(box)
        v.setContentsMargins(22, 18, 22, 18)
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

    def _files_header(self) -> QHBoxLayout:
        h = QHBoxLayout()
        h.addWidget(label(t("sec_files"), "section"))
        h.addStretch(1)
        if self.busy:
            stop = button(t("btn_stop"), "secondary", "x")
            stop.setEnabled(not self._stopped)
            stop.clicked.connect(self.stop)
            h.addWidget(stop)
        if self.items:
            c = button(t("btn_clear"), "ghost", "trash")
            c.setEnabled(not self.busy)
            c.clicked.connect(self.clear)
            h.addWidget(c)
        return h

    def _empty(self) -> QWidget:
        box = GlassFrame(radius=16)
        v = QVBoxLayout(box)
        v.setContentsMargins(24, 26, 24, 26)
        v.setSpacing(6)
        v.addWidget(label(t("empty_title"), "card_title"), 0, Qt.AlignmentFlag.AlignHCenter)
        v.addWidget(label(t("empty_body"), "tagline"), 0, Qt.AlignmentFlag.AlignHCenter)
        return box

    def _action_bar(self) -> QWidget:
        box = GlassFrame(radius=18, strength=1.2)
        h = QHBoxLayout(box)
        h.setContentsMargins(24, 18, 24, 18)
        h.setSpacing(24)

        opts = QVBoxLayout()
        opts.setSpacing(4)
        group = QButtonGroup(box)
        for mode, key in (("copy", "mode_copy"), ("replace", "mode_replace")):
            rb = QRadioButton(t(key))
            rb.setChecked(self.mode == mode)
            rb.setEnabled(not self.busy)
            rb.toggled.connect(lambda on, m=mode: on and self.set_mode(m))
            group.addButton(rb)
            opts.addWidget(rb)
            hint = label(t(f"{key}_hint"), "hint", wrap=True)
            hint.setContentsMargins(26, 0, 0, 6)
            opts.addWidget(hint)
        cb = QCheckBox(t("lightweight"))
        cb.setChecked(self.lightweight)
        cb.setEnabled(not self.busy)
        cb.toggled.connect(self.set_lightweight)
        opts.addWidget(cb)
        lh = label(t("lightweight_hint"), "hint", wrap=True)
        lh.setContentsMargins(26, 0, 0, 0)
        opts.addWidget(lh)
        h.addLayout(opts, 1)

        n = sum(1 for i in self.items.values() if i.cleanable)
        text = t("btn_clean_none") if n == 0 else (
            t("btn_clean_one") if n == 1 else t("btn_clean", n=n))
        go = button(text, "get_lg", "scrub", icon_color="#000000")
        go.setEnabled(n > 0 and not self.busy and self.mat2.available)
        go.clicked.connect(self.clean)
        h.addWidget(go, 0, Qt.AlignmentFlag.AlignVCenter)
        return box

    def _limits(self) -> QWidget:
        box = GlassFrame(radius=16)
        v = QVBoxLayout(box)
        v.setContentsMargins(22, 16, 22, 16)
        v.setSpacing(6)
        v.addWidget(label(t("sec_limits"), "section"))
        for key in ("limit_content", "limit_pdf", "limit_images", "limit_identity"):
            row = QHBoxLayout()
            row.setSpacing(8)
            ic = QLabel()
            ic.setPixmap(glyph_pixmap("point", 13, "#6f6f75"))
            row.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
            row.addWidget(label(t(key), "hint", wrap=True), 1)
            v.addLayout(row)
        return box
