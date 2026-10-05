"""Filesystem store: data/sites/<site_id>/{corpus/,graph.json,search.json,docs.json,meta.json}"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "sites"


def site_id_for(url: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", url.lower()).strip("-")[:48]
    return f"{slug}-{hashlib.sha256(url.encode()).hexdigest()[:8]}"


def site_dir(site_id: str) -> Path:
    return DATA / site_id


def list_sites() -> list[dict]:
    if not DATA.exists():
        return []
    out = []
    for d in sorted(DATA.iterdir()):
        meta = d / "meta.json"
        if meta.exists():
            m = json.loads(meta.read_text())
            out.append({"site_id": d.name, **m})
    return out


def save_site(site_id: str, base_url: str, built: dict) -> dict:
    d = site_dir(site_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "graph.json").write_text(json.dumps(built["graph"], ensure_ascii=False))
    (d / "search.json").write_text(json.dumps(built["search"], ensure_ascii=False))
    (d / "docs.json").write_text(json.dumps(built["docs"], ensure_ascii=False))
    meta = {
        "url": base_url,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        **built["report"],
    }
    (d / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False))
    return meta


def load(site_id: str, name: str) -> dict:
    p = site_dir(site_id) / f"{name}.json"
    if not p.exists():
        raise KeyError(f"unknown site '{site_id}' (have: {[s['site_id'] for s in list_sites()]})")
    return json.loads(p.read_text())
