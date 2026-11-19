"""Command line entry point: ``bahi serve`` and friends."""

from __future__ import annotations

import argparse
import json
import os
import threading
import webbrowser
from pathlib import Path

from . import __version__

DEFAULT_DATA = Path(os.environ.get("BAHI_DATA", Path.home() / ".bahi"))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="bahi", description="Open-source double-entry accounting with GST.")
    parser.add_argument("--version", action="version", version=f"bahi {__version__}")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA, help=f"data directory (default {DEFAULT_DATA})")
    sub = parser.add_subparsers(dest="cmd")

    serve = sub.add_parser("serve", help="run the web app (default)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8642)
    serve.add_argument("--no-browser", action="store_true", help="don't open a browser tab")

    sub.add_parser("demo", help="create the Demo Traders sample company")
    sub.add_parser("list", help="list companies in the data directory")
    exp = sub.add_parser("export", help="write a company backup as JSON")
    exp.add_argument("company")
    exp.add_argument("output", type=Path)

    args = parser.parse_args(argv)
    cmd = args.cmd or "serve"

    if cmd == "serve":
        import uvicorn

        from .api import create_app

        url = f"http://{args.host}:{args.port}/"
        print(f"Bahi {__version__} - data in {args.data.expanduser()}\nOpen {url}")
        if args.host not in ("127.0.0.1", "localhost"):
            print("Warning: Bahi has no login. Only listen on a network you trust.")
        if not getattr(args, "no_browser", False):
            threading.Timer(1.0, lambda: webbrowser.open(url)).start()
        uvicorn.run(create_app(args.data), host=args.host, port=args.port, log_level="warning")
        return

    from .db import Store

    store = Store(args.data)
    if cmd == "demo":
        from . import demo

        cid = store.new_id("Demo Traders")
        conn = store.create(cid)
        demo.populate(conn)
        conn.close()
        print(f"Created {cid} in {store.data_dir}")
    elif cmd == "list":
        from .books import company

        for cid in store.ids():
            conn = store.open(cid)
            info = company(conn)
            conn.close()
            print(f"{cid:30} {info['name']:40} books from {info['books_from']}")
    elif cmd == "export":
        from .transfer import export_company

        conn = store.open(args.company)
        args.output.write_text(json.dumps(export_company(conn), indent=1))
        conn.close()
        print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
