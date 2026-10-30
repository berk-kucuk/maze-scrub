"""Entry point. Single instance per user, like the other Maze apps.

A second launch — Dolphin's "Remove metadata" on more files while the window
is open — hands its paths to the running window over the singleton socket
instead of opening another one.
"""
from __future__ import annotations

import os
import sys

from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import QApplication

from mazescrub.core import settings
from mazescrub.gui.i18n import set_language
from mazescrub.gui.icons import app_logo
from mazescrub.gui.theme import stylesheet
from mazescrub.gui.window import MainWindow

_RUNTIME_DIR = os.environ.get("XDG_RUNTIME_DIR", "")
_SINGLETON = (os.path.join(_RUNTIME_DIR, "maze-scrub-singleton")
              if _RUNTIME_DIR and os.path.isdir(_RUNTIME_DIR)
              else f"maze-scrub-singleton-{os.getuid()}")
_MAX_MESSAGE = 4 * 1024 * 1024


def _install_excepthook(app: QApplication) -> None:
    """PyQt6 aborts the whole process on an exception escaping a slot. A bug
    in one button must never take the app down, so log it, tell the user,
    and keep running."""
    import traceback

    def hook(etype, value, tb) -> None:
        traceback.print_exception(etype, value, tb, file=sys.stderr)
        sys.stderr.flush()
        try:
            from PyQt6.QtWidgets import QMessageBox
            from mazescrub.gui.i18n import t
            QMessageBox.warning(app.activeWindow(), app.applicationDisplayName(),
                                f"{etype.__name__}: {value}\n\n{t('bug_body')}")
        except Exception:  # noqa: BLE001 — the hook itself must never raise
            pass
    sys.excepthook = hook


def _paths(argv: list[str]) -> list[str]:
    return [os.path.abspath(a) for a in argv[1:] if not a.startswith("--")]


def _send_to_running_instance(paths: list[str]) -> bool:
    sock = QLocalSocket()
    sock.connectToServer(_SINGLETON)
    if not sock.waitForConnected(300):
        return False
    # NUL-separated: the one byte a Linux path cannot contain.
    sock.write(b"show\0" + "\0".join(paths).encode("utf-8", "surrogateescape"))
    sock.flush()
    sock.waitForBytesWritten(1000)
    sock.disconnectFromServer()
    return True


def run() -> int:
    from mazescrub import cli
    if cli.wants_cli(sys.argv[1:]):
        return cli.main(sys.argv[1:])

    set_language(settings.load()["language"])
    for arg in sys.argv[1:]:
        if arg.startswith("--lang="):
            set_language(arg.split("=", 1)[1])

    app = QApplication(sys.argv)
    app.setApplicationName("Maze Scrub")
    app.setApplicationDisplayName("Maze Scrub")
    app.setOrganizationName("maze")
    app.setDesktopFileName("maze-scrub")
    app.setWindowIcon(app_logo())
    _install_excepthook(app)

    paths = _paths(sys.argv)
    if _send_to_running_instance(paths):
        return 0
    QLocalServer.removeServer(_SINGLETON)
    singleton = QLocalServer()
    singleton.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    singleton.listen(_SINGLETON)

    app.setStyleSheet(stylesheet())
    window = MainWindow()

    def _incoming() -> None:
        conn = singleton.nextPendingConnection()
        if conn is None:
            return
        buf = bytearray()

        def read() -> None:
            buf.extend(bytes(conn.readAll()))
            if len(buf) > _MAX_MESSAGE:
                conn.abort()

        def finished() -> None:
            read()
            parts = bytes(buf).split(b"\0")
            got = [p.decode("utf-8", "surrogateescape") for p in parts[1:] if p]
            window.showNormal()
            window.raise_()
            window.activateWindow()
            if got:
                window.add_paths(got)
            conn.deleteLater()
        conn.readyRead.connect(read)
        conn.disconnected.connect(finished)
    singleton.newConnection.connect(_incoming)

    window.show()
    if paths:
        window.add_paths(paths)
    code = app.exec()
    _shutdown(window)
    return code


def _shutdown(window) -> None:
    """Tear down in a fixed order. Left to the garbage collector, a Qt object
    can be destroyed after the QApplication and crash the interpreter on exit."""
    import gc
    from PyQt6.QtCore import QCoreApplication, QEvent, QThreadPool
    QThreadPool.globalInstance().waitForDone(30_000)
    window.hide()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    del window
    gc.collect()
