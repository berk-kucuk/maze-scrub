"""GUI tests, offscreen: strings, and the window driven through a full run.

The full run uses the fake libmat2 (tests/fake_libmat2) through the real
helper, so add → read → clean is exercised exactly as a click would.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FAKE = Path(__file__).resolve().parent / "fake_libmat2"

from PyQt6.QtCore import QThreadPool  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from mazescrub.gui import i18n  # noqa: E402

_app = QApplication.instance() or QApplication([])


class I18nTestCase(unittest.TestCase):
    def test_same_keys(self):
        en, tr = set(i18n.STRINGS["en"]), set(i18n.STRINGS["tr"])
        self.assertEqual(en - tr, set(), "missing in Turkish")
        self.assertEqual(tr - en, set(), "missing in English")

    def test_every_string_formats(self):
        """A placeholder that collides with t()'s own parameter, or differs
        between languages, is a crash in whichever language nobody tried."""
        for lang in ("en", "tr"):
            i18n.set_language(lang)
            for key, text in i18n.STRINGS[lang].items():
                names = set(re.findall(r"{(\w+)}", text))
                self.assertEqual(names, set(re.findall(r"{(\w+)}", i18n.STRINGS["en"][key])),
                                 key)
                i18n.t(key, **{n: "x" for n in names})
        i18n.set_language("en")

    def test_every_error_reason_has_text(self):
        for reason in ("readonly_dir", "clean_failed", "trash_failed", "changed",
                       "rename_failed"):
            self.assertIn(f"e_{reason}", i18n.STRINGS["en"])


class WindowTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="maze-scrub-gui-"))
        self.files = self.tmp / "files"
        self.files.mkdir()
        (self.files / "photo.jpg").write_text("GPSLatitude=41\nMake=Apple\nPIXELS\n")
        (self.files / "plain.jpg").write_text("PIXELS\n")
        (self.files / "notes.xyz").write_text("x\n")
        self._env = mock.patch.dict(os.environ, {
            "MAZE_SCRUB_TEST_LIBPATH": str(FAKE),
            "XDG_CONFIG_HOME": str(self.tmp / "config"),
            "XDG_DATA_HOME": str(self.tmp / "data")})
        self._env.start()
        from mazescrub.gui.window import MainWindow
        self.w = MainWindow()
        self._pump()

    def tearDown(self):
        self._pump()
        self.w.close()
        self.w.deleteLater()
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _pump(self, until=None, timeout: float = 10.0):
        end = time.time() + timeout
        while time.time() < end:
            _app.processEvents()
            QThreadPool.globalInstance().waitForDone(20)
            if until is None and not self.w.busy and time.time() > end - timeout + 0.3:
                return
            if until is not None and until():
                return

    def _item(self, name):
        return next(i for i in self.w.items.values() if i.path.name == name)

    def test_add_read_clean(self):
        self.assertTrue(self.w.mat2.available)
        self.w.add_paths([str(self.files)])
        self._pump(lambda: self.w.items and all(i.report for i in self.w.items.values())
                   and not self.w.busy)
        photo = self._item("photo.jpg")
        self.assertEqual(photo.report.categories, ["location", "device"])
        self.assertFalse(self._item("plain.jpg").cleanable)      # nothing to remove
        self.assertFalse(self._item("notes.xyz").report.supported)

        self.w.toggle(photo)                                      # renders the fields
        self.w.clean()
        self._pump(lambda: photo.result is not None and not self.w.busy)
        self.assertTrue(photo.result.ok)
        self.assertTrue((self.files / "photo.cleaned.jpg").exists())
        self.assertFalse(photo.cleanable, "a cleaned file is not offered again")

    def test_stop_marks_the_rest_unread_and_does_not_resume(self):
        from mazescrub.core import engine
        for i in range(engine.CHUNK * 6):
            (self.files / f"many{i}.jpg").write_text("Make=Apple\nPIXELS\n")
        self.w.add_paths([str(self.files)])
        self._pump(lambda: self.w.busy)
        self.w.stop()
        self._pump(lambda: not self.w.busy)
        self._pump(timeout=0.5)
        self.assertFalse(self.w.busy, "resumed on its own after Stop")
        reports = [i.report for i in self.w.items.values()]
        self.assertTrue(all(r is not None for r in reports))
        # Re-adding the folder reads what was skipped.
        self.w.add_paths([str(self.files)])
        self._pump(lambda: not self.w.busy and all(
            i.report and i.report.error != "cancelled" for i in self.w.items.values()), 30)
        self.assertFalse(any(i.report.error == "cancelled" for i in self.w.items.values()))

    def test_rows_are_reused_between_renders(self):
        self.w.add_paths([str(self.files)])
        self._pump(lambda: self.w.items and all(i.report for i in self.w.items.values())
                   and not self.w.busy)
        self.w._render()
        before = {k: id(r) for k, (_s, r) in self.w._rows.items()}
        self.w._render()
        self.assertEqual(before, {k: id(r) for k, (_s, r) in self.w._rows.items()})

    def test_language_switch_rebuilds(self):
        self.w.set_lang("tr")
        self.assertEqual(i18n.language(), "tr")
        self.w.set_lang("en")


if __name__ == "__main__":
    unittest.main(verbosity=2)
