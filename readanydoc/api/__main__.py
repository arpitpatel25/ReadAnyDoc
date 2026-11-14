"""``python -m readanydoc.api [--host H] [--port P]`` (same as ``readanydoc serve``)."""

from __future__ import annotations

import argparse
import sys


def serve(host: str = "127.0.0.1", port: int = 8000, log_level: str = "info") -> int:
    import uvicorn

    from .app import create_app
    uvicorn.run(create_app(), host=host, port=port, log_level=log_level)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="readanydoc.api")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args(argv)
    return serve(args.host, args.port)


if __name__ == "__main__":
    sys.exit(main())
