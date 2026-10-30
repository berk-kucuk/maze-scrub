"""The only code that touches libmat2. Runs under the system Python.

mat2 is an Arch package built against /usr/bin/python3 with GObject, Cairo and
Poppler bindings; Maze apps run on /opt/maze/venv, which has none of those. So
the app never imports libmat2: it starts this script with the system
interpreter (`python3 -I`), sends one JSON request on stdin and reads the
reply on stdout: one JSON line per file, written the moment that file is done,
so a folder of thousands shows up row by row instead of all at the end. Paths
travel inside the JSON, never as argv, so a file name starting with "-" or full
of spaces cannot be read as an option. Output is ASCII-escaped: a file name
that is not valid UTF-8 travels as surrogate escapes instead of crashing the
write.

Standard library plus libmat2 only, and every failure is answered in JSON —
an exception escaping here would reach the app as a blank reply.

Requests:
  {"op": "status"}
  {"op": "inspect", "paths": [...]}
  {"op": "clean", "lightweight": bool,
   "jobs": [{"path": ..., "output": ...}, ...]}
"""
from __future__ import annotations

import json
import os
import sys

# Tests point this at a stand-in libmat2; -I keeps PYTHONPATH from doing it.
_libpath = os.environ.get("MAZE_SCRUB_TEST_LIBPATH")
if _libpath:
    sys.path.insert(0, _libpath)

_MAX_VALUE = 400       # characters kept per metadata value
_MAX_FIELDS = 2000     # fields kept per file (an archive can hold thousands)


def _flatten(meta, prefix: str = "") -> list[list[str]]:
    """mat2 nests metadata for archives and documents: {member: {key: value}}."""
    out: list[list[str]] = []
    if not isinstance(meta, dict):
        return out
    for key, value in meta.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            out.extend(_flatten(value, prefix=f"{name} › "))
        else:
            text = str(value)
            if len(text) > _MAX_VALUE:
                text = text[:_MAX_VALUE] + "…"
            out.append([name, text])
        if len(out) >= _MAX_FIELDS:
            break
    return out[:_MAX_FIELDS]


def _parser(path: str):
    from libmat2 import parser_factory
    try:
        parser, mime = parser_factory.get_parser(path)
    except ValueError as e:          # mat2's answer for a corrupt file
        return None, "", f"{e}"
    return parser, mime or "", ""


def _inspect_one(path: str) -> dict:
    out = {"path": path, "supported": False, "mime": "", "fields": [], "error": ""}
    if not os.path.isfile(path) or not os.access(path, os.R_OK):
        out["error"] = "unreadable"
        return out
    try:
        parser, mime, error = _parser(path)
        out["mime"] = mime
        if parser is None:
            out["error"] = error
            return out
        out["supported"] = True
        out["fields"] = _flatten(parser.get_meta() or {})
    except Exception as e:  # noqa: BLE001 — reported, never raised
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def _clean_one(job: dict, lightweight: bool) -> dict:
    path, output = job.get("path", ""), job.get("output", "")
    out = {"path": path, "output": output, "ok": False, "error": "", "remaining": []}
    if not path or not output:
        out["error"] = "bad request"
        return out
    if os.path.lexists(output):
        # The app picks a free name; this is the guard against a race.
        out["error"] = "output exists"
        return out
    try:
        parser, _mime, error = _parser(path)
        if parser is None:
            out["error"] = error or "unsupported"
            return out
        parser.output_filename = output
        parser.lightweight_cleaning = bool(lightweight)
        if hasattr(parser, "sandbox"):
            parser.sandbox = True       # exiftool/ffmpeg under bubblewrap
        ok = bool(parser.remove_all())
        if not ok or not os.path.isfile(output):
            out["error"] = "mat2 could not clean this file"
            if os.path.lexists(output):
                os.unlink(output)
            return out
        # Verify instead of trusting: re-read what mat2 wrote.
        check, _m, _e = _parser(output)
        out["remaining"] = _flatten(check.get_meta() or {}) if check else []
        out["ok"] = True
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
        try:
            if os.path.lexists(output):
                os.unlink(output)
        except OSError:
            pass
    return out


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _version(libmat2) -> str:
    v = getattr(libmat2, "__version__", "")
    if v:
        return str(v)
    try:
        from importlib.metadata import version
        return version("mat2")
    except Exception:  # noqa: BLE001
        return ""


def _status() -> dict:
    import libmat2
    deps = {}
    try:
        for name, info in libmat2.check_dependencies().items():
            if isinstance(info, dict):
                deps[name] = {"found": bool(info.get("found")),
                              "required": bool(info.get("required"))}
            else:
                deps[name] = {"found": bool(info), "required": False}
    except Exception:  # noqa: BLE001 — older libmat2 without the helper
        pass
    return {"version": _version(libmat2), "deps": deps}


def main() -> int:
    try:
        req = json.loads(sys.stdin.read() or "{}")
        op = req.get("op")
        try:
            import warnings
            warnings.simplefilter("ignore")    # PyGIWarning noise on stderr
            import libmat2  # noqa: F401
        except ImportError:
            _emit({"error": "no_mat2"})
            return 0
        if op == "status":
            _emit(_status())
        elif op == "inspect":
            for path in req.get("paths", []):
                _emit(_inspect_one(str(path)))
        elif op == "clean":
            lw = bool(req.get("lightweight"))
            for job in req.get("jobs", []):
                if isinstance(job, dict):
                    _emit(_clean_one(job, lw))
        else:
            _emit({"error": "bad_op"})
    except Exception as e:  # noqa: BLE001
        _emit({"error": f"{type(e).__name__}: {e}"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
