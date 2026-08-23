"""Local preview server.

In production the site is one flat directory: apps/web/* at the root with data/
copied in beside index.html. Rather than duplicating files locally, this server
serves apps/web and transparently maps /data/* to the repo's data/ directory.

    python tools/serve.py [--port 8765]
"""
from __future__ import annotations

import argparse
import functools
import http.server
import os
import socketserver
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "apps" / "web"
DATA = ROOT / "data"


class Handler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        clean = path.split("?", 1)[0].split("#", 1)[0]
        if clean.startswith("/data/"):
            target = DATA / clean[len("/data/"):]
            return str(target)
        return super().translate_path(path)

    def end_headers(self) -> None:
        # No caching locally, so an edit always shows up on reload.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:
        if "200" not in (args[1] if len(args) > 1 else ""):
            super().log_message(fmt, *args)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()

    os.chdir(WEB)
    handler = functools.partial(Handler, directory=str(WEB))
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", args.port), handler) as httpd:
        print("serving %s  (+ /data -> %s)" % (WEB, DATA))
        print("http://127.0.0.1:%d/" % args.port)
        httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
