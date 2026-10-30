"""Engine tests: the real helper under the real system Python, in temp dirs.

libmat2 is replaced by tests/fake_libmat2 (see its docstring), so the JSON
protocol, the subprocess, the Trash and every "never lose the original" path
run for real. When the real mat2 is installed, RealMat2TestCase also checks
the helper against it.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FAKE = Path(__file__).resolve().parent / "fake_libmat2"

from mazescrub import cli  # noqa: E402
from mazescrub.core import engine, settings, trash  # noqa: E402

PHOTO = ("GPSLatitude=41.0082\nGPSLongitude=28.9784\nMake=Apple\nModel=iPhone 15\n"
         "Software=17.1\nCreateDate=2026:10:30 10:00:00\nExposureTime=1/120\n"
         "PIXELS\n")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="maze-scrub-test-"))
        self.data = self.tmp / "data"
        self.files = self.tmp / "files"
        self.data.mkdir()
        self.files.mkdir()
        self._env = mock.patch.dict(os.environ, {
            "MAZE_SCRUB_TEST_LIBPATH": str(FAKE),
            "XDG_DATA_HOME": str(self.data),
            "XDG_CONFIG_HOME": str(self.tmp / "config"),
        })
        self._env.start()

    def tearDown(self):
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, name: str, text: str = PHOTO) -> Path:
        p = self.files / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p


class CategorizeTestCase(unittest.TestCase):
    def test_categories(self):
        cases = {
            "GPSLatitude": "location", "GPS Date Stamp": "location",
            "Author": "person", "Last Modified By": "person",
            "CreatorTool": "software", "Creator": "person", "Producer": "software",
            "Make": "device", "Camera Model Name": "device", "SerialNumber": "device",
            "CreateDate": "time", "ModifyDate": "time",
            "ExposureTime": "", "ImageWidth": "", "BitsPerSample": "",
            "word/docProps/core.xml › creator": "person",
        }
        for key, want in cases.items():
            self.assertEqual(engine.categorize(key), want, key)


class SplitRemainingTestCase(unittest.TestCase):
    def test_new_values_are_not_reported_as_leftovers(self):
        F = engine.Field
        original = [F("author", "Berk"), F("creation-date", "1792487640"), F("format", "PDF-1.3")]
        remaining = [F("creation-date", "1793400000"), F("format", "PDF-1.5"), F("author", "Berk")]
        kept, rewritten = engine.split_remaining(original, remaining)
        self.assertEqual([f.key for f in kept], ["author"])
        self.assertEqual([f.key for f in rewritten], ["creation-date", "format"])


class InspectTestCase(Base):
    def test_status_reports_the_helper_and_missing_tools(self):
        st = engine.status()
        self.assertTrue(st.available)
        self.assertEqual(st.version, "0.0-fake")
        self.assertEqual(st.missing, ["ffmpeg"])

    def test_fields_and_categories(self):
        r = engine.inspect([self.write("photo.jpg")])[0]
        self.assertTrue(r.supported)
        keys = {f.key: f for f in r.fields}
        self.assertEqual(keys["GPSLatitude"].value, "41.0082")
        self.assertEqual(r.categories, ["location", "device", "time", "software"])

    def test_archive_members_are_flattened(self):
        r = engine.inspect([self.write("doc.odt", "meta.xml/initial-creator=Berk\nTEXT\n")])[0]
        self.assertEqual(r.fields[0].key, "meta.xml › initial-creator")
        self.assertEqual(r.fields[0].category, "person")

    def test_unsupported_and_corrupt_are_reported_not_raised(self):
        reports = engine.inspect([self.write("notes.xyz"), self.write("broken.bad")])
        self.assertFalse(reports[0].supported)
        self.assertFalse(reports[1].supported)
        self.assertIn("corrupt", reports[1].error)

    def test_hostile_file_names_travel_safely(self):
        p = self.write("-rf --help ; $(x).jpg")
        r = engine.inspect([p])[0]
        self.assertTrue(r.supported)
        self.assertEqual(r.path, p)

    def test_many_files_are_batched_with_progress(self):
        files = [self.write(f"p{i}.jpg") for i in range(engine.CHUNK * 2 + 3)]
        seen = []
        reports = engine.inspect(files, progress=lambda d, n: seen.append((d, n)))
        self.assertEqual(len(reports), len(files))
        self.assertEqual(seen[-1], (len(files), len(files)))
        self.assertGreater(len(seen), 1)

    def test_no_mat2_is_a_clear_error(self):
        with mock.patch.dict(os.environ, {"MAZE_SCRUB_TEST_LIBPATH": str(self.tmp)}):
            # an empty dir instead of the fake: no libmat2 importable
            if _real_mat2():
                self.skipTest("the real mat2 is installed")
            with self.assertRaises(engine.ScrubError) as cm:
                engine.inspect([self.write("photo.jpg")])
            self.assertEqual(cm.exception.reason, "no_mat2")
            self.assertFalse(engine.status().available)

    def test_missing_python_is_a_clear_error(self):
        with mock.patch.dict(os.environ, {"MAZE_SCRUB_PYTHON": "/nonexistent/python3"}):
            with self.assertRaises(engine.ScrubError) as cm:
                engine.inspect([self.write("photo.jpg")])
            self.assertEqual(cm.exception.reason, "no_python")


class LargeFolderTestCase(Base):
    """What a folder of thousands of files used to break."""

    def test_results_stream_in_before_the_run_ends(self):
        files = [self.write(f"p{i}.jpg") for i in range(engine.CHUNK * 3)]
        seen = []
        reports = engine.inspect(files, on_item=seen.append)
        self.assertEqual(len(seen), len(files))
        self.assertEqual([r.path for r in reports], files, "order is kept")

    def test_cancel_stops_and_kills_the_helpers(self):
        import threading
        files = [self.write(f"p{i}.jpg") for i in range(engine.CHUNK * 4)]
        cancel = threading.Event()
        seen = []

        def on_item(r):
            seen.append(r)
            if len(seen) == 3:
                cancel.set()
        with self.assertRaises(engine.ScrubError) as cm:
            engine.inspect(files, on_item=on_item, cancel=cancel)
        self.assertEqual(cm.exception.reason, "cancelled")
        self.assertLess(len(seen), len(files))

    def test_cancel_in_the_middle_of_cleaning_leaves_no_half_files(self):
        import threading
        files = [self.write(f"p{i}.jpg") for i in range(engine.CHUNK * 3)]
        cancel = threading.Event()
        done = []

        def on_item(r):
            done.append(r)
            if len(done) == 5:
                cancel.set()
        results = engine.clean(files, cancel=cancel, on_item=on_item)
        self.assertIn("cancelled", {r.error for r in results})
        self.assertTrue(all(r.ok for r in results if r.error != "cancelled"))
        cleaned = sorted(self.files.glob("*.cleaned.jpg"))
        # Every .cleaned file on disk is a finished one.
        for c in cleaned:
            self.assertEqual(c.read_text(), "PIXELS\n")
        for f in files:
            self.assertEqual(f.read_text(), PHOTO, "an original was touched")

    def test_a_failing_chunk_does_not_sink_the_others(self):
        good = [self.write(f"g{i}.jpg") for i in range(engine.CHUNK)]
        real = engine._stream

        def flaky(req, cancel=None, timeout=engine.TIMEOUT_PER_FILE):
            if any("bad" in p for p in req.get("paths", [])):
                raise engine.ScrubError("timeout")
            yield from real(req, cancel, timeout)
        bad = [self.write(f"bad{i}.jpg") for i in range(3)]
        with mock.patch.object(engine, "_stream", side_effect=flaky):
            reports = engine.inspect(good + bad)
        self.assertTrue(all(r.supported for r in reports[:len(good)]))
        self.assertTrue(all(r.error == "timeout" for r in reports[len(good):]))

    def test_non_utf8_file_names(self):
        name = os.fsdecode(b"foto-\xff\xfe.jpg")
        p = self.files / name
        p.write_text(PHOTO)
        r = engine.inspect([p])[0]
        self.assertTrue(r.supported, r.error)
        self.assertEqual(r.path, p)
        res = engine.clean([p])[0]
        self.assertTrue(res.ok, res)

    def test_real_version_without_dunder(self):
        self.assertTrue(engine.status().version)


class ExpandTestCase(Base):
    def test_folders_recurse_and_links_are_not_followed(self):
        a = self.write("a.jpg")
        self.write("sub/b.jpg")
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "secret.jpg").write_text(PHOTO)
        os.symlink(outside, self.files / "link")
        os.symlink(outside / "secret.jpg", self.files / "file-link.jpg")
        files, truncated = engine.expand([self.files, a])
        self.assertFalse(truncated)
        self.assertEqual(sorted(p.name for p in files), ["a.jpg", "b.jpg"])

    def test_cap(self):
        for i in range(5):
            self.write(f"{i}.jpg")
        with mock.patch.object(engine, "MAX_FILES", 3):
            files, truncated = engine.expand([self.files])
        self.assertEqual(len(files), 3)
        self.assertTrue(truncated)


class CleanCopyTestCase(Base):
    def test_copy_mode_keeps_the_original(self):
        p = self.write("photo.jpg")
        r = engine.clean([p])[0]
        self.assertTrue(r.ok, r)
        self.assertEqual(r.output, self.files / "photo.cleaned.jpg")
        self.assertEqual(p.read_text(), PHOTO)
        self.assertEqual(r.output.read_text(), "PIXELS\n")
        self.assertEqual(r.remaining, [])
        self.assertFalse(r.trashed)

    def test_existing_cleaned_file_is_never_overwritten(self):
        p = self.write("photo.jpg")
        mine = self.write("photo.cleaned.jpg", "MY OWN FILE\n")
        r = engine.clean([p])[0]
        self.assertEqual(r.output, self.files / "photo.cleaned-2.jpg")
        self.assertEqual(mine.read_text(), "MY OWN FILE\n")

    def test_lightweight_leftovers_are_reported(self):
        r = engine.clean([self.write("photo.jpg")], lightweight=True)[0]
        self.assertTrue(r.ok)
        self.assertEqual([f.key for f in r.remaining], ["Software"])

    def test_failure_leaves_nothing_behind(self):
        p = self.write("photo.jpg", "Author=me\nFAIL\n")
        r = engine.clean([p])[0]
        self.assertFalse(r.ok)
        self.assertEqual(r.error, "clean_failed")
        self.assertEqual(sorted(x.name for x in self.files.iterdir()), ["photo.jpg"])

    def test_read_only_folder(self):
        p = self.write("ro/photo.jpg")
        os.chmod(p.parent, 0o555)
        try:
            if os.access(p.parent, os.W_OK):
                self.skipTest("running as root")
            r = engine.clean([p])[0]
            self.assertEqual(r.error, "readonly_dir")
        finally:
            os.chmod(p.parent, 0o755)


class CleanReplaceTestCase(Base):
    def _trash_files(self):
        d = self.data / "Trash" / "files"
        return sorted(x.name for x in d.iterdir()) if d.exists() else []

    def test_replace_puts_the_original_in_the_trash(self):
        p = self.write("photo.jpg")
        os.chmod(p, 0o640)
        r = engine.clean([p], mode="replace")[0]
        self.assertTrue(r.ok and r.replaced and r.trashed, r)
        self.assertEqual(p.read_text(), "PIXELS\n")
        self.assertEqual(p.stat().st_mode & 0o777, 0o640)
        self.assertEqual(self._trash_files(), ["photo.jpg"])
        self.assertEqual((self.data / "Trash" / "files" / "photo.jpg").read_text(), PHOTO)
        info = (self.data / "Trash" / "info" / "photo.jpg.trashinfo").read_text()
        self.assertIn(f"Path={p}", info)
        # No temp file left behind.
        self.assertEqual(sorted(x.name for x in self.files.iterdir()), ["photo.jpg"])

    def test_trash_name_collisions(self):
        a = self.write("photo.jpg")
        engine.clean([a], mode="replace")
        a.write_text(PHOTO)
        engine.clean([a], mode="replace")
        self.assertEqual(self._trash_files(), ["photo.2.jpg", "photo.jpg"])

    def test_trash_failure_keeps_the_original(self):
        p = self.write("photo.jpg")
        with mock.patch.object(trash, "move_to_trash",
                               side_effect=trash.TrashError("nope")):
            r = engine.clean([p], mode="replace")[0]
        self.assertTrue(r.ok)
        self.assertFalse(r.replaced or r.trashed)
        self.assertEqual(r.error, "trash_failed")
        self.assertEqual(p.read_text(), PHOTO)
        self.assertEqual(r.output, self.files / "photo.cleaned.jpg")

    def test_file_changed_during_cleaning_is_not_replaced(self):
        p = self.write("photo.jpg")
        real = engine._stream

        def edit_meanwhile(req, cancel=None, timeout=engine.TIMEOUT_PER_FILE):
            for msg in real(req, cancel, timeout):
                if req.get("op") == "clean":
                    p.write_text(PHOTO + "NEW EDIT\n")
                yield msg
        with mock.patch.object(engine, "_stream", side_effect=edit_meanwhile):
            r = engine.clean([p], mode="replace")[0]
        self.assertEqual(r.error, "changed")
        self.assertIn("NEW EDIT", p.read_text())
        self.assertFalse(r.trashed)
        self.assertEqual(r.output.name, "photo.cleaned.jpg")

    def test_failed_clean_in_replace_mode_touches_nothing(self):
        p = self.write("photo.jpg", "Author=me\nFAIL\n")
        r = engine.clean([p], mode="replace")[0]
        self.assertFalse(r.ok)
        self.assertEqual(self._trash_files(), [])
        self.assertEqual(sorted(x.name for x in self.files.iterdir()), ["photo.jpg"])


class CliTestCase(Base):
    def test_clean_json(self):
        import io
        import json
        from contextlib import redirect_stdout
        p = self.write("photo.jpg")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(["--clean", "--json", str(p)])
        self.assertEqual(code, 0)
        out = json.loads(buf.getvalue())
        self.assertTrue(out["files"][0]["ok"])
        self.assertTrue(out["files"][0]["output"].endswith("photo.cleaned.jpg"))

    def test_failure_exit_code(self):
        import io
        from contextlib import redirect_stdout
        p = self.write("photo.jpg", "FAIL\nA=b\n")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["--clean", str(p)]), 1)


class SettingsTestCase(Base):
    def test_defaults_and_garbage(self):
        self.assertEqual(settings.load()["mode"], "copy")
        settings.save(mode="replace", language="tr")
        self.assertEqual(settings.load()["mode"], "replace")
        settings.save(mode="rm -rf", lightweight="yes")
        s = settings.load()
        self.assertEqual(s["mode"], "copy")
        self.assertIs(s["lightweight"], False)


def _real_mat2() -> bool:
    import subprocess
    r = subprocess.run(["/usr/bin/python3", "-I", "-c", "import libmat2"],
                       capture_output=True)
    return r.returncode == 0


@unittest.skipUnless(_real_mat2(), "mat2 is not installed")
class RealMat2TestCase(unittest.TestCase):
    """The helper against the real libmat2: a JPEG with EXIF in, none out."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="maze-scrub-real-"))
        self._env = mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(self.tmp / "data")})
        self._env.start()
        os.environ.pop("MAZE_SCRUB_TEST_LIBPATH", None)

    def tearDown(self):
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_real_jpeg_roundtrip(self):
        import subprocess
        if not shutil.which("exiftool"):
            self.skipTest("exiftool missing")
        img = self.tmp / "photo.jpg"
        subprocess.run(["magick", "-size", "32x32", "xc:gray", str(img)], check=True)
        subprocess.run(["exiftool", "-q", "-overwrite_original", "-Artist=Berk",
                        "-GPSLatitude=41.0", "-GPSLatitudeRef=N", str(img)], check=True)
        r = engine.inspect([img])[0]
        self.assertTrue(r.supported, r.error)
        self.assertIn("person", r.categories)
        self.assertIn("location", r.categories)
        res = engine.clean([img])[0]
        self.assertTrue(res.ok, res)
        after = engine.inspect([res.output])[0]
        self.assertFalse({"person", "location"} & set(after.categories), after.fields)


if __name__ == "__main__":
    unittest.main(verbosity=2)
