#!/usr/bin/env python3
"""Maze Scrub — see what your files reveal about you, then remove it."""
import sys

if __name__ == "__main__":
    from mazescrub.gui.app import run
    sys.exit(run())
