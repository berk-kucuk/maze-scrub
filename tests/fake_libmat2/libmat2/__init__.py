"""A stand-in for libmat2, used only by the test suite.

Files are text: `key=value` lines are metadata, `member/key=value` lines are
metadata of an archive member, everything else is content. Cleaning writes the
content lines only — except in lightweight mode, which keeps `Software=` the
way real lightweight cleaning leaves some fields behind.
"""
__version__ = "0.0-fake"


def check_dependencies():
    return {"Cairo": {"found": True, "required": True},
            "ffmpeg": {"found": False, "required": False}}
