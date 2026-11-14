"""HEIC / HEIF images: decoded to PNG, then read like any image.

No HEIC decoder ships with readanydoc: Pillow has none, and the usual add-on wheel (pillow-heif) bundles
LGPL (libheif, libde265) and GPL (x265) libraries, which this project does not redistribute (docs/decisions.md).
Decoders used when present, in order:
1. ``pillow_heif``, if the operator installed it (``pip install pillow-heif``);
2. ``sips``, the image tool that ships with macOS;
3. ``heif-convert`` (libheif's command-line tool, e.g. ``apt install libheif-examples``).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from .errors import IntakeError, unsupported


def decoder() -> Optional[str]:
    """Name of the decoder that will be used, or None."""
    if os.environ.get("READANYDOC_HEIC_DECODER"):
        want = os.environ["READANYDOC_HEIC_DECODER"]
        return None if want == "none" else want
    try:
        import pillow_heif  # noqa: F401
        return "pillow_heif"
    except Exception:
        pass
    for tool in ("sips", "heif-convert"):
        if shutil.which(tool):
            return tool
    return None


def to_png(path: Path, timeout: int = 120) -> Path:
    """Decode ``path`` to a PNG in a new temporary directory. Raises the 415 error without a decoder."""
    how = decoder()
    if how is None:
        raise unsupported("heic", "no HEIC decoder is installed on this server",
                          "Install pillow-heif (pip install pillow-heif) or libheif's heif-convert, or convert "
                          "the image to JPG or PNG and try again.")
    out = Path(tempfile.mkdtemp(prefix="readanydoc-heic-")) / (Path(path).stem + ".png")
    try:
        if how == "pillow_heif":
            import pillow_heif
            from PIL import Image
            pillow_heif.register_heif_opener()
            with Image.open(path) as im:
                im.convert("RGB").save(out)
        else:
            exe = shutil.which(how) or how
            cmd = [exe, "-s", "format", "png", str(path), "--out", str(out)] if how == "sips" else [exe, str(path), str(out)]
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
            if proc.returncode != 0:
                raise IntakeError(f"cannot open the heic file: it is damaged or is not really a heic file ({how} exit {proc.returncode})")
    except IntakeError:
        raise
    except Exception as e:
        raise IntakeError(f"cannot open the heic file: it is damaged or is not really a heic file ({type(e).__name__})") from e
    if not out.exists() or out.stat().st_size == 0:
        raise IntakeError("cannot open the heic file: it is damaged or is not really a heic file (no image decoded)")
    return out
