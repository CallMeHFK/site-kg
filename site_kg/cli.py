"""CLI: ingest / serve / stats. See `python -m site_kg.cli <cmd> --help`."""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from .viewer import SCENES


def _die(message: str) -> None:
    raise SystemExit(f"live: {message}")


def _resolve_live_target(target: str, *, local: bool, replay: bool) -> tuple[str, str]:
    """Map one positional argument onto url | local | replay before any server
    starts, so an ambiguous invocation fails loudly instead of guessing."""
    target = (target or "").strip()
    if not target:
        _die("target is required")
    if local and replay:
        _die("--local and --replay are mutually exclusive")
    if "://" in target:
        if local:
            _die("--local takes a directory path, not a URL")
        if replay:
            _die("--replay takes a site id, not a URL")
        if urlsplit(target).scheme.lower() not in ("http", "https"):
            _die("URL target must be http(s)")
        return "url", target
    if local:
        root = Path(target).expanduser()
        if not root.is_dir():
            _die(f"not a directory: {target}")
        return "local", str(root.resolve())
    from . import store
    # directory hint before the site lookup: a Windows path is not a valid
    # site id, and site_dir rejects those with ValueError
    if Path(target).expanduser().is_dir():
        _die(f"'{target}' is a local directory; pass --local")
    try:
        known = (store.site_dir(target) / "meta.json").exists()
    except ValueError:
        known = False
    if not known:
        _die(f"unknown site '{target}'; pass a URL or --local <path>")
    return "replay", target


def _wait_live(handle) -> None:
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        handle.close()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="site-kg")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ingest")
    p.add_argument("url")
    p.add_argument("--max-pages", type=int, default=None,
                   help="page cap (default: 200, or 2000 with --dev-crawl)")
    p.add_argument("--max-depth", type=int, default=None,
                   help="BFS depth cap (default: 4, or 8 with --dev-crawl)")
    p.add_argument("--no-robots", action="store_true")
    p.add_argument("--dev-crawl", action="store_true",
                   help="DEVELOPMENT/TESTING mode for sites you own or are "
                        "authorized to test: ignores robots.txt and politeness "
                        "delays, higher concurrency, deeper defaults "
                        "(--max-pages 2000 --max-depth 8 unless overridden)")
    p.add_argument("--relations", default=None,
                   help="derived relation dimensions, selected per node: auto | llm | all "
                        "| off | comma list (default: $SITE_KG_RELATIONS or auto)")
    lp = sub.add_parser("ingest-local")
    lp.add_argument("path")
    lp.add_argument("--max-files", type=int, default=2000)
    lp.add_argument("--no-gitignore", action="store_true")
    lp.add_argument("--relations", default=None,
                    help="derived relation dimensions, selected per node: auto | llm | all "
                         "| off | comma list (default: $SITE_KG_RELATIONS or auto)")
    s = sub.add_parser("serve")
    s.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8766)
    lv = sub.add_parser("live")
    lv.add_argument("target", help="http(s) URL, local directory (--local) or site id")
    lv.add_argument("--scene", choices=SCENES, default=None,
                    help="scene for an immediate start; omit a URL to open the launcher")
    lv.add_argument("--local", action="store_true", help="treat target as a directory")
    lv.add_argument("--replay", action="store_true", help="replay a stored site id")
    lv.add_argument("--max-nodes", type=int, default=None)
    lv.add_argument("--port", type=int, default=0)
    lv.add_argument("--token", default=None)
    lv.add_argument("--block-private", action="store_true",
                    help="refuse /api/start targets that point at the private "
                         "network: loopback, 10/8, 172.16/12, 192.168/16, "
                         "169.254/16, fc00::/7, unspecified addresses, and "
                         "domains that resolve there (or fail to resolve). "
                         "Default off so local dev servers and intranets stay "
                         "crawlable; use on untrusted networks")
    lv.add_argument("--dev-crawl", action="store_true",
                    help="DEVELOPMENT/TESTING mode for sites you own or are "
                         "authorized to test: ignores robots.txt and politeness "
                         "delays, deeper crawl defaults")
    t = sub.add_parser("stats")
    t.add_argument("site_id", nargs="?")
    v = sub.add_parser("render")
    v.add_argument("site_id")
    v.add_argument("--scene", choices=SCENES, default="planets")
    v.add_argument("--max-nodes", type=int, default=None)
    v.add_argument("--external-data", action="store_true",
                   help="write sk-*.bin beside viewer.html instead of inlining")
    vb = sub.add_parser("render-build")
    vb.add_argument("site_id")
    args = ap.parse_args(argv)

    if args.cmd == "ingest":
        from .mcp_server import ingest_url
        dev = args.dev_crawl
        max_pages = args.max_pages if args.max_pages is not None else (2000 if dev else 200)
        max_depth = args.max_depth if args.max_depth is not None else (8 if dev else 4)
        print(json.dumps(asyncio.run(ingest_url(
            args.url, max_pages=max_pages, max_depth=max_depth,
            concurrency=8 if dev else 4,
            respect_robots=not args.no_robots, relations=args.relations, dev=dev)),
            indent=1, ensure_ascii=False))
    elif args.cmd == "ingest-local":
        from .mcp_server import ingest_local
        print(json.dumps(asyncio.run(ingest_local(
            args.path, max_files=args.max_files,
            respect_gitignore=not args.no_gitignore, relations=args.relations)),
            indent=1, ensure_ascii=False))
    elif args.cmd == "serve":
        from .mcp_server import serve
        serve(args.transport, args.host, args.port)
    elif args.cmd == "live":
        from . import store
        from .live import serve_live
        kind, target = _resolve_live_target(args.target, local=args.local,
                                            replay=args.replay)
        opts = {"scene": args.scene or "planets", "max_nodes": args.max_nodes,
                "port": args.port, "token": args.token,
                "block_private": args.block_private, "dev_crawl": args.dev_crawl}
        if kind == "url":
            site_id = store.site_id_for(target)
            if args.scene:
                handle = serve_live(site_id, source=target, **opts)
            else:
                # no scene: open the launcher, address prefilled, scene chooser
                handle = serve_live(site_id, title=target, **opts)
        elif kind == "local":
            handle = serve_live(store.site_id_for(f"file://{target}"),
                                source=target, **opts)
        else:
            handle = serve_live(target, replay=True, **opts)
        url = handle.url
        if args.token:
            url = f"{url}?{urlencode({'token': args.token})}"
        print(url, flush=True)
        _wait_live(handle)
    elif args.cmd == "render":
        from . import store
        from .viewer import render_viewer
        out = store.site_dir(args.site_id) / "viewer.html"
        try:
            render_viewer(store.load(args.site_id, "graph"), store.load(args.site_id, "meta"),
                          out, scene=args.scene, max_nodes=args.max_nodes,
                          external_data=args.external_data)
        except (KeyError, ValueError) as e:
            # same failure contract as render-build below and the MCP tools:
            # clean error, no traceback (unknown site or malformed site id)
            raise SystemExit(f"render failed: {e}")
        print(out)
    elif args.cmd == "render-build":
        from . import store
        from .buildview import render_build_viewer
        out = store.site_dir(args.site_id) / "build.html"
        try:
            render_build_viewer(store.load(args.site_id, "graph"),
                                store.load(args.site_id, "meta"), out)
        except (KeyError, ValueError) as e:
            # same failure contract as the MCP render_build tool: clean error,
            # no traceback (empty graph or unknown site)
            raise SystemExit(f"render-build failed: {e}")
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
