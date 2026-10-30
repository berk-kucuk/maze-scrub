"""Move a file to the desktop Trash — the only way Maze Scrub ever lets go of
an original.

"Replace originals" must not mean "delete originals": a cleaned file can be
worse than the user expected (a rasterised PDF, a re-encoded JPEG), and the
way back has to be the Trash everyone already knows. Dolphin, Plasma and GNOME
all read the freedesktop.org Trash, so a file put there by the spec shows up
in Dolphin's Trash with its original location and a working "Restore".

Files on the home filesystem go to $XDG_DATA_HOME/Trash directly. Anything
else (a USB stick, another partition) is handed to `gio trash`, which knows
the per-volume `.Trash-$UID` rules. If neither works the caller keeps the
original — refusing is always safe, losing a file never is.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from urllib.parse import quote


class TrashError(OSError):
    pass


def home_trash() -> Path:
    data = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(data) / "Trash"


def _same_device(a: Path, b: Path) -> bool:
    try:
        return a.stat().st_dev == b.stat().st_dev
    except OSError:
        return False


def _info_text(path: Path) -> str:
    return ("[Trash Info]\n"
            f"Path={quote(str(path), safe='/')}\n"
            f"DeletionDate={time.strftime('%Y-%m-%dT%H:%M:%S')}\n")


def _trash_home(path: Path, trash: Path) -> Path:
    files, info = trash / "files", trash / "info"
    for d in (trash, files, info):
        d.mkdir(mode=0o700, exist_ok=True)

    stem, suffix = path.stem, path.suffix
    for n in range(1, 10_000):
        name = path.name if n == 1 else f"{stem}.{n}{suffix}"
        info_file = info / f"{name}.trashinfo"
        # The spec's lock: whoever creates the .trashinfo first owns the name.
        try:
            fd = os.open(info_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        try:
            with os.fdopen(fd, "w") as f:
                f.write(_info_text(path))
            if os.path.lexists(files / name):
                os.unlink(info_file)
                continue
            os.rename(path, files / name)
        except BaseException:
            try:
                os.unlink(info_file)
            except OSError:
                pass
            raise
        return files / name
    raise TrashError("no free name in the Trash")


def move_to_trash(path: Path) -> Path | None:
    """Trash `path`. Returns where it went (None when gio did it). Raises
    TrashError when the file could not be trashed — and is then untouched."""
    path = Path(os.path.abspath(path))
    trash = home_trash()
    trash.mkdir(parents=True, exist_ok=True)
    if _same_device(path.parent, trash):
        try:
            return _trash_home(path, trash)
        except OSError as e:
            raise TrashError(str(e)) from e
    try:
        r = subprocess.run(["gio", "trash", "--", str(path)],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        raise TrashError(str(e)) from e
    if r.returncode != 0 or os.path.lexists(path):
        raise TrashError(r.stderr.strip() or "gio trash failed")
    return None
