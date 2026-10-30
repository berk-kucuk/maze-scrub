"""Command line: the same engine without a window.

    maze-scrub                          open the window
    maze-scrub FILE|FOLDER...           open the window with these loaded
    maze-scrub --show FILE...           print what each file reveals
    maze-scrub --clean FILE...          write name.cleaned.ext next to each
    maze-scrub --clean --replace ...    clean in place, originals to the Trash
               --lightweight            mat2's lightweight mode
               --json                   machine-readable output (--show/--clean)

`--clean --json` is the interface for other Maze apps (HazeDrop scrubbing a
file before sending it): one JSON object per run on stdout, exit 0 only when
every file was cleaned.
"""
from __future__ import annotations

import json
import sys

from mazescrub import __version__
from mazescrub.core import engine


def wants_cli(argv: list[str]) -> bool:
    return any(a in ("--show", "--clean", "--version", "-h", "--help") for a in argv)


def _files(argv: list[str]):
    return [a for a in argv if not a.startswith("--")]


def main(argv: list[str]) -> int:
    if "--version" in argv:
        print(f"maze-scrub {__version__}")
        return 0
    if "-h" in argv or "--help" in argv:
        print(__doc__.strip())
        return 0
    as_json = "--json" in argv
    files, truncated = engine.expand(_files(argv))
    if not files:
        print("maze-scrub: no files given", file=sys.stderr)
        return 2
    try:
        if "--show" in argv:
            return _show(files, as_json)
        mode = "replace" if "--replace" in argv else "copy"
        return _clean(files, mode, "--lightweight" in argv, as_json)
    except engine.ScrubError as e:
        msg = {"no_mat2": "mat2 is not installed (sudo pacman -S mat2)",
               "no_python": "the system Python (/usr/bin/python3) is missing",
               "timeout": "mat2 took too long"}.get(e.reason, f"mat2 helper failed: {e.detail}")
        if as_json:
            print(json.dumps({"error": e.reason, "detail": msg}))
        else:
            print(f"maze-scrub: {msg}", file=sys.stderr)
        return 1
    finally:
        if truncated:
            print(f"maze-scrub: stopped at {engine.MAX_FILES} files", file=sys.stderr)


def _show(files, as_json: bool) -> int:
    reports = engine.inspect(files)
    if as_json:
        print(json.dumps({"files": [{
            "path": str(r.path), "supported": r.supported, "error": r.error,
            "categories": r.categories,
            "fields": [{"key": f.key, "value": f.value, "category": f.category}
                       for f in r.fields]} for r in reports]}, ensure_ascii=False))
        return 0
    for r in reports:
        print(f"\n{r.path}")
        if not r.supported:
            print(f"  not supported{': ' + r.error if r.error else ''}")
        elif not r.fields:
            print("  no metadata found")
        for f in r.fields:
            tag = f"[{f.category}] " if f.category else ""
            print(f"  {tag}{f.key}: {f.value}")
    return 0


def _clean(files, mode: str, lightweight: bool, as_json: bool) -> int:
    results = engine.clean(files, mode=mode, lightweight=lightweight)
    if as_json:
        print(json.dumps({"files": [{
            "path": str(r.path), "ok": r.ok, "output": str(r.output) if r.output else None,
            "replaced": r.replaced, "trashed": r.trashed, "error": r.error,
            "detail": r.detail, "remaining": [f.key for f in r.remaining]}
            for r in results]}, ensure_ascii=False))
    else:
        for r in results:
            if r.ok:
                where = "replaced in place" if r.replaced else f"→ {r.output}"
                left = f", {len(r.remaining)} field(s) remain" if r.remaining else ""
                note = f" ({r.error})" if r.error else ""
                print(f"cleaned {r.path} {where}{left}{note}")
            else:
                print(f"FAILED  {r.path}: {r.error} {r.detail}".rstrip())
    return 0 if all(r.ok for r in results) else 1
