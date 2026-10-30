"""Inspect and clean files through mat2 — the app's whole contract with it.

libmat2 is never imported here (see helper/mat2_helper.py for why): every call
starts the helper under the system Python and exchanges one JSON document.

Two rules carry the design:

* **Show before removing.** `inspect()` reports every field mat2 can read, each
  tagged with what it gives away (location, person, device, time, software),
  so the user sees the GPS position before deciding anything.
* **Never lose the original.** "copy" writes `name.cleaned.ext` next to it.
  "replace" cleans into a hidden temp file, re-reads it, moves the original to
  the Trash and only then puts the cleaned file in its place. Any step that
  fails leaves the original where it was and the cleaned copy beside it.
"""
from __future__ import annotations

import json
import os
import queue
import re
import secrets
import select
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Iterator

from mazescrub.core import trash

HELPER = Path(__file__).resolve().parents[1] / "helper" / "mat2_helper.py"

MAX_FILES = 5000     # one drop of a home folder must not freeze the window
CHUNK = 40           # files per helper run; a helper that dies loses at most this
TIMEOUT_PER_FILE = 120   # seconds without a reply line before the helper is killed
# mat2 spends its time in exiftool/ffmpeg, one process per file, so a few
# helpers side by side make a large folder several times faster.
WORKERS = max(1, min(4, (os.cpu_count() or 2) // 2))

CATEGORIES = ("location", "person", "device", "time", "software")


class ScrubError(Exception):
    """reason: no_python | no_mat2 | helper | timeout | cancelled"""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


# ── what a field gives away ──────────────────────────────────────────────────

# Checked in order; the first match wins. "CreatorTool" is software, not a
# person, and "ExposureTime" is a camera setting, not a timestamp.
_RULES: list[tuple[str, re.Pattern]] = [
    ("software", re.compile(r"creator ?tool|software|producer|application|encoder|"
                            r"generator|writing ?app|muxing ?app|handler|program|"
                            r"history ?software|tool ?name", re.I)),
    ("location", re.compile(r"gps|latitude|longitude|altitude|location|geo|city|"
                            r"country|province|state$|sub-?location|time ?zone|"
                            r"offset ?time", re.I)),
    ("person", re.compile(r"author|creator|artist|owner|last ?modified ?by|"
                          r"modifiedby|company|manager|copyright|by-?line|writer|"
                          r"user|e-?mail|credit|contact|rights|publisher|"
                          r"comment|description|keywords|subject|title", re.I)),
    ("device", re.compile(r"\bmake\b|^make|model|serial|lens|camera|host ?computer|"
                          r"device|body ?serial|firmware|unique ?id|uuid|"
                          r"document ?id|instance ?id", re.I)),
    ("time", re.compile(r"date|(?<!exposure )(?<!exposure)time(?!zone)|created|"
                        r"modified|creation|revision", re.I)),
]


def categorize(key: str) -> str:
    """The category a field belongs to, or "" when it reveals nothing personal."""
    leaf = key.split(" › ")[-1].strip()
    if re.search(r"exposure ?time|shutter|aperture|f-?number|iso", leaf, re.I):
        return ""
    for cat, rx in _RULES:
        if rx.search(leaf):
            return cat
    return ""


@dataclass
class Field:
    key: str
    value: str
    category: str = ""


@dataclass
class Report:
    path: Path
    supported: bool = False
    mime: str = ""
    fields: list[Field] = field(default_factory=list)
    error: str = ""

    @property
    def categories(self) -> list[str]:
        found = {f.category for f in self.fields if f.category}
        return [c for c in CATEGORIES if c in found]


@dataclass
class CleanResult:
    path: Path
    ok: bool = False
    output: Path | None = None      # where the cleaned file is now
    remaining: list[Field] = field(default_factory=list)
    replaced: bool = False          # the cleaned file now sits at `path`
    trashed: bool = False           # the original is in the Trash
    error: str = ""                 # reason key; see i18n "e_*"
    detail: str = ""


@dataclass
class Mat2Status:
    available: bool
    version: str = ""
    missing: list[str] = field(default_factory=list)   # optional tools not found
    reason: str = ""


# ── talking to the helper ────────────────────────────────────────────────────

def _python() -> str:
    return os.environ.get("MAZE_SCRUB_PYTHON", "/usr/bin/python3")


def _env() -> dict:
    keep = ("PATH", "HOME", "LANG", "LC_ALL", "XDG_RUNTIME_DIR", "TMPDIR",
            "MAZE_SCRUB_TEST_LIBPATH")
    return {k: os.environ[k] for k in keep if k in os.environ}


def _stream(request: dict, cancel=None,
            timeout: float = TIMEOUT_PER_FILE) -> Iterator[dict]:
    """Run the helper and yield its reply lines as they arrive.

    The helper is killed — never left running — when the caller stops
    iterating, when `cancel` is set, or when it stays silent for `timeout`
    seconds. stderr goes to a temp file rather than a pipe: a pipe nobody
    reads fills up and stalls the helper mid-folder.
    """
    py = _python()
    if not os.access(py, os.X_OK):
        raise ScrubError("no_python", py)
    err = tempfile.TemporaryFile()
    try:
        proc = subprocess.Popen([py, "-I", str(HELPER)], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=err, env=_env())
    except OSError as e:
        err.close()
        raise ScrubError("helper", str(e)) from e
    try:
        proc.stdin.write(json.dumps(request).encode())
        proc.stdin.close()
        buf = b""
        quiet = 0.0
        while True:
            if cancel is not None and cancel.is_set():
                raise ScrubError("cancelled")
            ready, _, _ = select.select([proc.stdout], [], [], 0.25)
            if not ready:
                quiet += 0.25
                if quiet >= timeout:
                    raise ScrubError("timeout")
                continue
            quiet = 0.0
            chunk = os.read(proc.stdout.fileno(), 65536)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    raise ScrubError("helper", line[-300:].decode(errors="replace"))
                if isinstance(msg, dict) and "error" in msg and "path" not in msg:
                    raise ScrubError("no_mat2" if msg["error"] == "no_mat2" else "helper",
                                     "" if msg["error"] == "no_mat2" else str(msg["error"]))
                if isinstance(msg, dict):
                    yield msg
        proc.wait(timeout=10)
        if proc.returncode not in (0, None):
            err.seek(0)
            raise ScrubError("helper", err.read()[-400:].decode(errors="replace").strip()
                             or f"exit {proc.returncode}")
    except (OSError, subprocess.SubprocessError) as e:
        raise ScrubError("helper", str(e)) from e
    finally:
        if proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.SubprocessError:
                pass
        err.close()
        for pipe in (proc.stdin, proc.stdout):
            try:
                pipe.close()
            except (OSError, ValueError):
                pass


def _call(request: dict, timeout: float) -> dict:
    for msg in _stream(request, timeout=timeout):
        return msg
    raise ScrubError("helper", "no reply")


def status() -> Mat2Status:
    try:
        reply = _call({"op": "status"}, timeout=30)
    except ScrubError as e:
        return Mat2Status(False, reason=e.reason)
    deps = reply.get("deps") or {}
    missing = sorted(name for name, d in deps.items()
                     if isinstance(d, dict) and not d.get("found"))
    return Mat2Status(True, str(reply.get("version", "")), missing)


_DONE = object()


class _Either:
    """is_set() when either the caller cancelled or this run is stopping."""

    def __init__(self, outer: threading.Event | None):
        self._outer = outer
        self._own = threading.Event()

    def set(self) -> None:
        self._own.set()

    def is_set(self) -> bool:
        return self._own.is_set() or (self._outer is not None and self._outer.is_set())


def _fan_out(requests: list[tuple[dict, list[str]]], cancel: threading.Event | None
             ) -> Iterator[tuple[str, dict]]:
    """Run several helper requests side by side; yield (path, reply) as each
    file finishes, on the caller's thread.

    A request that fails (timeout, helper crash) does not stop the others:
    its unanswered files come back as {"error": ...} so the caller can show
    them as failed — retrying them forever is how a bad file once spun the
    window in a loop. Only "mat2 is missing" and cancellation end the run.
    """
    q: queue.Queue = queue.Queue()
    # Our own flag for "stop the other helpers", seen together with the
    # caller's cancel — setting the caller's event here would leave it set and
    # cancel their next run before it started.
    stop = _Either(cancel)

    def run(req: dict, paths: list[str]) -> None:
        answered: set[str] = set()
        try:
            for msg in _stream(req, stop):
                path = msg.get("path")
                if isinstance(path, str):
                    answered.add(path)
                    q.put((path, msg))
        except ScrubError as e:
            if e.reason in ("no_mat2", "no_python", "cancelled"):
                q.put(e)
                return
            for p in paths:
                if p not in answered:
                    q.put((p, {"path": p, "error": e.detail or e.reason}))
            return
        for p in paths:
            if p not in answered:
                q.put((p, {"path": p, "error": "no reply"}))

    pending = len(requests)
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for req, paths in requests:
            pool.submit(lambda r=req, ps=paths: (run(r, ps), q.put(_DONE)))
        try:
            while pending:
                item = q.get()
                if item is _DONE:
                    pending -= 1
                elif isinstance(item, ScrubError):
                    stop.set()
                    raise item
                else:
                    yield item
        finally:
            # Leaving early (an error, or the caller gave up): stop the rest
            # before the pool waits for them.
            if pending:
                stop.set()


# ── files ────────────────────────────────────────────────────────────────────

def expand(paths: Iterable[Path | str]) -> tuple[list[Path], bool]:
    """Files to work on: the regular files given, plus everything under the
    folders given. Symlinks are not followed — cleaning "a folder" must not
    wander through a link into somewhere else. Returns (files, truncated)."""
    out: list[Path] = []
    seen: set[str] = set()

    def add(p: Path) -> bool:
        key = os.path.abspath(p)
        if key not in seen:
            seen.add(key)
            out.append(Path(key))
        return len(out) >= MAX_FILES

    for raw in paths:
        p = Path(raw)
        try:
            if p.is_symlink():
                continue
            if p.is_file():
                if add(p):
                    return out, True
            elif p.is_dir():
                for root, dirs, files in os.walk(p, followlinks=False):
                    dirs.sort()
                    for name in sorted(files):
                        f = Path(root) / name
                        if f.is_symlink() or not f.is_file():
                            continue
                        if add(f):
                            return out, True
        except OSError:
            continue
    return out, False


def _fields(raw) -> list[Field]:
    out = []
    for item in raw or []:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            k, v = str(item[0]), str(item[1])
            out.append(Field(k, v, categorize(k)))
    return out


def _chunks(items: list, n: int) -> list[list]:
    return [items[i:i + n] for i in range(0, len(items), n)]


def inspect(paths: list[Path], progress: Callable[[int, int], None] | None = None,
            on_item: Callable[[Report], None] | None = None,
            cancel: threading.Event | None = None) -> list[Report]:
    """Read every file's metadata. `on_item` gets each Report the moment it
    is ready; the return value is all of them, in the order given."""
    by_path: dict[str, Report] = {}
    requests = [({"op": "inspect", "paths": [str(p) for p in chunk]},
                 [str(p) for p in chunk]) for chunk in _chunks(paths, CHUNK)]
    for path, f in _fan_out(requests, cancel):
        r = Report(path=Path(path), supported=bool(f.get("supported")),
                   mime=str(f.get("mime", "")), fields=_fields(f.get("fields")),
                   error=str(f.get("error", "")))
        by_path[path] = r
        if on_item:
            on_item(r)
        if progress:
            progress(len(by_path), len(paths))
    return [by_path.get(str(p)) or Report(path=p, error="no reply") for p in paths]


def split_remaining(original: list[Field], remaining: list[Field]
                    ) -> tuple[list[Field], list[Field]]:
    """(carried over, rewritten): what is still in a cleaned file, split by
    whether it came from the original.

    mat2 rebuilds some formats from scratch, and the new file gets fresh
    values of its own — a PDF re-drawn by Cairo has a new creation-date (the
    moment of cleaning) and its own PDF version. Those say nothing about the
    user and must not be reported as a failed clean; a field whose value is
    the original's must.
    """
    before = {(f.key, f.value) for f in original}
    kept = [f for f in remaining if (f.key, f.value) in before]
    rewritten = [f for f in remaining if (f.key, f.value) not in before]
    return kept, rewritten


def cleaned_name(path: Path, reserved: set[str] | None = None) -> Path:
    """`photo.jpg` → `photo.cleaned.jpg`, never a name that already exists or
    that another file in the same run was promised."""
    stem, suffix = path.stem, path.suffix
    for n in range(1, 10_000):
        tag = "cleaned" if n == 1 else f"cleaned-{n}"
        candidate = path.with_name(f"{stem}.{tag}{suffix}")
        if not os.path.lexists(candidate) and str(candidate) not in (reserved or ()):
            return candidate
    raise ScrubError("helper", "no free output name")


def _temp_name(path: Path) -> Path:
    # Hidden, unpredictable, and with the real suffix: mat2 picks its re-check
    # parser by extension.
    return path.with_name(f".{path.stem}.scrub-{secrets.token_hex(4)}{path.suffix}")


def _fingerprint(path: Path) -> tuple | None:
    try:
        st = path.stat()
        return (st.st_ino, st.st_size, st.st_mtime_ns)
    except OSError:
        return None


def clean(paths: list[Path], mode: str = "copy", lightweight: bool = False,
          progress: Callable[[int, int], None] | None = None,
          on_item: Callable[[CleanResult], None] | None = None,
          cancel: threading.Event | None = None) -> list[CleanResult]:
    results: dict[str, CleanResult] = {}
    prepared: dict[str, tuple[CleanResult, Path, tuple | None]] = {}
    reserved: set[str] = set()
    jobs: list[dict] = []
    done = 0

    def finish(res: CleanResult) -> None:
        nonlocal done
        done += 1
        if on_item:
            on_item(res)
        if progress:
            progress(done, len(paths))

    for p in paths:
        res = CleanResult(path=p)
        results[str(p)] = res
        if not os.access(p.parent, os.W_OK):
            res.error = "readonly_dir"
            finish(res)
            continue
        if mode == "replace":
            out = _temp_name(p)
        else:
            out = cleaned_name(p, reserved)
        reserved.add(str(out))
        prepared[str(p)] = (res, out, _fingerprint(p))
        jobs.append({"path": str(p), "output": str(out)})

    requests = [({"op": "clean", "lightweight": bool(lightweight), "jobs": chunk},
                 [j["path"] for j in chunk]) for chunk in _chunks(jobs, CHUNK)]
    try:
        for path, f in _fan_out(requests, cancel):
            if path not in prepared:
                continue
            res, out, before = prepared.pop(path)
            if not f.get("ok"):
                res.error, res.detail = "clean_failed", str(f.get("error", ""))
            else:
                res.ok = True
                res.remaining = _fields(f.get("remaining"))
                if mode == "replace":
                    _put_in_place(res, out, before)
                else:
                    res.output = out
            finish(res)
    except ScrubError as e:
        # Stopping is an answer, not a failure: the files done so far keep
        # their results, the rest come back as "cancelled", untouched.
        if e.reason != "cancelled":
            raise
    finally:
        for res, out, _before in prepared.values():
            res.error = "cancelled" if cancel is not None and cancel.is_set() else "clean_failed"
            # `out` was a name nobody had when the run began, so anything
            # there now is mat2's half-written output from a killed helper —
            # not a cleaned file, and not something to leave looking like one.
            try:
                if os.path.lexists(out):
                    os.unlink(out)
            except OSError:
                pass
    return [results[str(p)] for p in paths]


def _put_in_place(res: CleanResult, tmp: Path, before: tuple | None) -> None:
    """Swap the cleaned temp file in for the original, original to the Trash.

    Every early return leaves the original untouched and the cleaned file
    under its visible `.cleaned` name, so nothing is ever lost or hidden.
    """
    original = res.path

    def keep_as_copy(reason: str, detail: str = "") -> None:
        dest = cleaned_name(original)
        try:
            os.rename(tmp, dest)
            res.output = dest
        except OSError:
            res.output = tmp
        res.error, res.detail = reason, detail

    # Changed while mat2 was working on it: the cleaned file is of an older
    # version, so replacing would throw the user's newest edit away.
    if before is None or _fingerprint(original) != before:
        keep_as_copy("changed")
        return
    try:
        os.chmod(tmp, original.stat().st_mode & 0o7777)
    except OSError:
        pass
    try:
        trash.move_to_trash(original)
    except trash.TrashError as e:
        keep_as_copy("trash_failed", str(e))
        return
    res.trashed = True
    try:
        os.rename(tmp, original)
    except OSError as e:
        # The original is safe in the Trash; say where the cleaned one is.
        keep_as_copy("rename_failed", str(e))
        return
    res.replaced = True
    res.output = original
