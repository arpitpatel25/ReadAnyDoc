"""One line, once ever, after the first successful command-line parse or extract, asking for a GitHub star.

Shown only when stderr is a terminal (never in scripts, pipes, CI, the server or the MCP server), never from the
Python library, and never when ``READANYDOC_NO_STAR`` is set. A marker file remembers that it was shown.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

URL = "https://github.com/arpitpatel25/ReadAnyDoc"
MESSAGE = f"If ReadAnyDoc helped, a star helps others find it: {URL}  (shown once)"


def marker() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "readanydoc" / "star-asked"


def maybe_ask(stream=None) -> bool:
    """Print the one-time line if this is an interactive terminal and it was never shown. Returns True if shown."""
    stream = stream or sys.stderr
    if os.environ.get("READANYDOC_NO_STAR") or os.environ.get("CI"):
        return False
    try:
        if not stream.isatty():
            return False
        m = marker()
        if m.exists():
            return False
        m.parent.mkdir(parents=True, exist_ok=True)
        m.write_text("1\n")
    except OSError:
        return False
    print(f"\n{MESSAGE}", file=stream)
    return True
