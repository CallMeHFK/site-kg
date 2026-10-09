"""CLI: ingest / serve / stats. See `python -m site_kg.cli <cmd> --help`."""
from __future__ import annotations

import argparse
import asyncio
import json


def main() -> None:
    ap = argparse.ArgumentParser(prog="site-kg")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ingest")
    p.add_argument("url")
    p.add_argument("--max-pages", type=int, default=200)
    p.add_argument("--max-depth", type=int, default=4)
    p.add_argument("--no-robots", action="store_true")
    lp = sub.add_parser("ingest-local")
    lp.add_argument("path")
    lp.add_argument("--max-files", type=int, default=2000)
    lp.add_argument("--no-gitignore", action="store_true")
    s = sub.add_parser("serve")
    s.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8766)
    t = sub.add_parser("stats")
    t.add_argument("site_id", nargs="?")
    v = sub.add_parser("render")
    v.add_argument("site_id")
    args = ap.parse_args()

    if args.cmd == "ingest":
        from .mcp_server import ingest_url
        print(json.dumps(asyncio.run(ingest_url(
            args.url, max_pages=args.max_pages, max_depth=args.max_depth,
            respect_robots=not args.no_robots)), indent=1, ensure_ascii=False))
    elif args.cmd == "ingest-local":
        from .mcp_server import ingest_local
        print(json.dumps(asyncio.run(ingest_local(
            args.path, max_files=args.max_files,
            respect_gitignore=not args.no_gitignore)), indent=1, ensure_ascii=False))
    elif args.cmd == "serve":
        from .mcp_server import serve
        serve(args.transport, args.host, args.port)
    elif args.cmd == "render":
        from . import store
        from .viewer import render_viewer
        out = store.site_dir(args.site_id) / "viewer.html"
        render_viewer(store.load(args.site_id, "graph"), store.load(args.site_id, "meta"), out)
        print(out)
    else:
        from . import store
        if args.site_id:
            from .mcp_server import site_stats
            print(json.dumps(site_stats(args.site_id), indent=1, ensure_ascii=False))
        else:
            print(json.dumps(store.list_sites(), indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
