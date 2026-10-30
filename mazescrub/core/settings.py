"""Per-user preferences, in ~/.config/maze-scrub/settings.json."""
from __future__ import annotations

import os
from pathlib import Path

from mazescrub.core import atomic

DEFAULTS = {
    "language": "en",       # "en" (default) or "tr"
    "mode": "copy",         # "copy": keep originals | "replace": originals to Trash
    "lightweight": False,   # mat2's lightweight cleaning
}
LANGUAGES = ("en", "tr")
MODES = ("copy", "replace")


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "maze-scrub"


def _path() -> Path:
    return config_dir() / "settings.json"


def load() -> dict:
    out = dict(DEFAULTS)
    data = atomic.read_json(_path())
    if isinstance(data, dict):
        for k, v in data.items():
            if k in DEFAULTS and type(v) is type(DEFAULTS[k]):
                out[k] = v
    if out["language"] not in LANGUAGES:
        out["language"] = "en"
    if out["mode"] not in MODES:
        out["mode"] = "copy"
    return out


def save(**values) -> None:
    try:
        config_dir().mkdir(parents=True, exist_ok=True, mode=0o700)
        atomic.write_json(_path(), {**load(), **values})
    except OSError:
        pass
