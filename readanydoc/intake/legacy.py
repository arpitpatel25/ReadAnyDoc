"""Legacy and foreign office formats (.doc, .xls, .ppt, .rtf, .odt, ...): converted to the modern OOXML
sibling by an external converter, then read by the native readers.

Converters, in order:
1. LibreOffice (``soffice``), found on PATH, in the macOS application folder, or named by
   ``READANYDOC_SOFFICE``. Handles every format in ``LEGACY_KINDS``.
2. ``textutil`` (ships with macOS): word-processing formats only (doc, dot, rtf, odt, wpd -> docx). Used
   when LibreOffice is absent, so a Mac without LibreOffice still reads DOC / RTF / ODT. Not for
   spreadsheets or presentations.
Without a converter the 415 error says what to install.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from .errors import IntakeError, unsupported
from .sniff import LEGACY_KINDS

TEXTUTIL_KINDS = ("doc", "dot", "rtf", "odt", "wpd")


def find_soffice() -> Optional[str]:
    env = os.environ.get("READANYDOC_SOFFICE")
    if env:
        return env if Path(env).exists() or shutil.which(env) else None
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    mac = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
    return str(mac) if mac.exists() else None


def find_textutil() -> Optional[str]:
    if os.environ.get("READANYDOC_NO_TEXTUTIL") == "1":
        return None
    return shutil.which("textutil")


def converter_for(kind: str) -> Optional[tuple[str, str]]:
    """("soffice" | "textutil", executable) for a legacy kind, or None when nothing can convert it."""
    if kind not in LEGACY_KINDS:
        return None
    soffice = find_soffice()
    if soffice:
        return "soffice", soffice
    textutil = find_textutil()
    if textutil and kind in TEXTUTIL_KINDS:
        return "textutil", textutil
    return None


def install_advice(kind: str) -> tuple[str, str]:
    """(why, what to do) for a legacy file that no converter on this machine can read. Names the exact
    package and command, and what is wrong with READANYDOC_SOFFICE when that is the reason."""
    import sys
    target = LEGACY_KINDS.get(kind, "docx").upper()
    env = os.environ.get("READANYDOC_SOFFICE")
    if env and not (Path(env).exists() or shutil.which(env)):
        why = (f".{kind} files are converted to {target} with LibreOffice, and READANYDOC_SOFFICE points to "
               f"{env!r}, which does not exist")
    else:
        why = (f".{kind} files are converted to {target} with LibreOffice (the 'soffice' program), which was "
               "not found on this server")
    if kind not in TEXTUTIL_KINDS and sys.platform == "darwin":
        why += " (macOS textutil converts only DOC / RTF / ODT, not spreadsheets or presentations)"
    if sys.platform == "darwin":
        how = "brew install --cask libreoffice"
    elif sys.platform.startswith("win"):
        how = "winget install TheDocumentFoundation.LibreOffice"
    else:
        how = ("apt-get install -y libreoffice-core libreoffice-writer libreoffice-calc libreoffice-impress "
               "(Debian / Ubuntu; in the Docker image add it to the Dockerfile), or dnf install libreoffice")
    hint = (f"Install LibreOffice on the server: {how}. If it is installed somewhere unusual, set "
            f"READANYDOC_SOFFICE to the full path of soffice and restart the server. Or convert the file to "
            f"{target} or PDF and send that.")
    return why, hint


def _looks_real(path: Path, kind: str) -> bool:
    try:
        head = path.read_bytes()[:4096]
    except OSError:
        return False
    if head.startswith(b"{\\rtf"):
        return True
    if kind in ("doc", "dot"):
        try:
            import olefile
            if not olefile.isOleFile(str(path)):
                return False
            ole = olefile.OleFileIO(str(path))
            try:
                return ole.exists("WordDocument")
            finally:
                ole.close()
        except Exception:
            return False
    if kind == "odt":
        import zipfile
        try:
            with zipfile.ZipFile(path) as z:
                return "content.xml" in z.namelist()
        except Exception:
            return False
    return kind == "wpd" and head.startswith(b"\xffWPC")


def convert(path: Path, kind: str, timeout: int = 180) -> tuple[Path, str]:
    """Convert to the modern OOXML sibling. Returns (new path, new kind) or raises the 415 error."""
    target = LEGACY_KINDS.get(kind)
    found = converter_for(kind)
    if target is None:
        raise unsupported(kind)
    if found is None:
        why, hint = install_advice(kind)
        raise unsupported(kind, why, hint)
    tool, exe = found
    if tool == "textutil" and not _looks_real(Path(path), kind):
        # textutil turns any bytes into a document; refuse what is plainly not the format it claims to be
        raise IntakeError(f"cannot open the {kind} file: it is damaged or is not really a {kind} file")
    out_dir = Path(tempfile.mkdtemp(prefix="readanydoc-convert-"))
    made = out_dir / f"{Path(path).stem}.{target}"
    if tool == "soffice":
        # a private profile directory: a second soffice (or a running desktop one) must not block this call
        cmd = [exe, f"-env:UserInstallation=file://{out_dir}/profile", "--headless", "--norestore",
               "--convert-to", target, "--outdir", str(out_dir), str(path)]
    else:
        cmd = [exe, "-convert", target, str(path), "-output", str(made)]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as e:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise IntakeError(f"conversion of the {kind!r} file failed ({type(e).__name__})") from e
    if proc.returncode != 0 or not made.exists() or made.stat().st_size == 0:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise IntakeError(f"conversion of the {kind!r} file failed ({tool} exit {proc.returncode})")
    return made, target
