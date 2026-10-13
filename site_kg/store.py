"""Filesystem store: data/sites/<site_id>/{corpus/,docs/,graph.json,search.json,meta.json}

Page bodies live in docs/<bucket>.json (1..256 sha-keyed buckets, count scaled
to corpus size and recorded in meta.json as docBuckets), not one monolithic
docs.json: a 75k-doc kernel tree produced a 975MB single file that made
get_page an 8s json.loads of everything. The corpus already shards by
chapter; the store shards by hash bucket so no corpus shape (drivers/ with
31k files) can recreate one giant shard. Sites built before sharding keep a
docs.json alongside and are still read, transparently.

Freshness without cache invalidation: every cache is keyed by the content's
own stat (mtime_ns/size of the shard or meta file, mtime of the docs dir for
the inventory), so a re-ingest from ANY process -- cli ingest-local, a crawl
job, another agent -- is picked up on the next read, and nothing needs a
cache_clear() hook in save_site. save_site writes every shard via tmp-file +
os.replace and commits meta.json (which carries docBuckets) last, so a
concurrent reader sees either the old or the new set, never torn JSON; the
residual window is a transient clean miss on a page whose bucket layout is
being swapped, and it self-heals when the save completes. Every bucket in
0..docBuckets-1 is written even when empty, so the on-disk shard inventory
always matches docBuckets and can stand in for a missing meta.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "sites"


def site_id_for(url: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", url.lower()).strip("-")[:48]
    return f"{slug}-{hashlib.sha256(url.encode()).hexdigest()[:8]}"


def site_dir(site_id: str) -> Path:
    return DATA / site_id


def reset_corpus(corpus_dir: Path) -> None:
    """Re-ingest must not leave ghost docs from deleted/renamed sources."""
    import shutil

    if corpus_dir.exists():
        shutil.rmtree(corpus_dir)


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


def _bucket_count(n_docs: int) -> int:
    """Shard count scales with corpus size: avg bucket stays ~2MB and a small
    site keeps a handful of files instead of 256 near-empty ones."""
    for floor, n in ((32768, 256), (1024, 64), (128, 16)):
        if n_docs >= floor:
            return n
    return 1


def _bucket(page_id: str, n_buckets: int = 1) -> str:
    return str(int(hashlib.sha256(page_id.encode()).hexdigest()[:8], 16) % n_buckets)


def _docs_dir(site_id: str) -> Path:
    return site_dir(site_id) / "docs"


def _write_json(path: Path, obj: dict, *, indent: int | None = None) -> None:
    """tmp file + os.replace: a concurrent reader never sees a torn JSON."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=indent))
    os.replace(tmp, path)


def save_site(site_id: str, base_url: str, built: dict,
              extra: dict | None = None) -> dict:
    d = site_dir(site_id)
    d.mkdir(parents=True, exist_ok=True)
    _write_json(d / "graph.json", built["graph"])
    _write_json(d / "search.json", built["search"])
    docs_dir = _docs_dir(site_id)
    docs_dir.mkdir(parents=True, exist_ok=True)
    n_buckets = _bucket_count(len(built["docs"]))
    keep = {str(b) for b in range(n_buckets)}
    for stale in docs_dir.glob("*.json"):  # bucket count shrank between ingests
        if stale.stem not in keep:
            stale.unlink()
    # all buckets are written even when empty: the shard inventory then always
    # equals docBuckets and a reader can derive the layout without meta.json
    buckets: dict[str, dict] = {b: {} for b in keep}
    for pid, doc in built["docs"].items():
        buckets[_bucket(pid, n_buckets)][pid] = doc
    for bucket, shard in buckets.items():
        _write_json(docs_dir / f"{bucket}.json", shard)
    # commit order for a concurrent reader: shards first, the legacy single
    # file goes away, then meta.json with docBuckets lands last
    (d / "docs.json").unlink(missing_ok=True)  # superseded single-file format
    meta = {
        "url": base_url,
        "docBuckets": n_buckets,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        **built["report"],
        **(extra or {}),
    }
    _write_json(d / "meta.json", meta, indent=1)
    return meta


def _unknown_site(site_id: str) -> KeyError:
    return KeyError(f"unknown site '{site_id}' (have: {[s['site_id'] for s in list_sites()]})")


@lru_cache(maxsize=64)
def _meta_cached(site_id: str, mtime_ns: int) -> dict:
    return json.loads((site_dir(site_id) / "meta.json").read_text())


def _meta(site_id: str) -> dict:
    """meta.json, cached by its own mtime: an out-of-process re-ingest is seen
    on the next read with no invalidation hook. Missing or torn (mid-save)
    reads as empty so get_doc falls back to the shard inventory."""
    try:
        st = (site_dir(site_id) / "meta.json").stat()
    except OSError:
        return {}
    try:
        return _meta_cached(site_id, st.st_mtime_ns)
    except (OSError, json.JSONDecodeError):
        return {}


@lru_cache(maxsize=64)
def _inventory_cached(site_id: str, dir_mtime_ns: int) -> tuple[str, ...]:
    return tuple(sorted(p.stem for p in _docs_dir(site_id).glob("*.json")))


def _shard_names(site_id: str) -> tuple[str, ...]:
    """Shard inventory, keyed by the docs dir mtime -- which every shard
    mutation bumps, since shards only change via unlink/os.replace in-place."""
    try:
        mtime = _docs_dir(site_id).stat().st_mtime_ns
    except OSError:
        return ()
    return _inventory_cached(site_id, mtime)


@lru_cache(maxsize=16)
def _shard_cached(site_id: str, bucket: str, mtime_ns: int, size: int) -> dict:
    return json.loads((_docs_dir(site_id) / f"{bucket}.json").read_text())


def get_doc(site_id: str, page_id: str) -> dict | None:
    """One page body: one hash bucket of the corpus, not the whole file."""
    d = site_dir(site_id)
    if not d.exists():
        raise _unknown_site(site_id)
    docs = _docs_dir(site_id)
    if docs.is_dir():
        names = _shard_names(site_id)
        n = _meta(site_id).get("docBuckets") or max(len(names), 1)
        p = docs / f"{_bucket(page_id, n)}.json"
        try:
            st = p.stat()
        except OSError:
            return None  # mid-save: this shard has not landed yet
        return _shard_cached(site_id, p.stem, st.st_mtime_ns, st.st_size).get(page_id)
    legacy = d / "docs.json"
    if legacy.exists():  # pre-shard site: fall back to the single-file read
        return json.loads(legacy.read_text()).get(page_id)
    return None


def iter_docs(site_id: str):
    """Stream (page_id, doc) pairs: every shard of a sharded site, or the
    legacy docs.json of a pre-shard one (never both)."""
    if _docs_dir(site_id).is_dir():
        for p in sorted(_docs_dir(site_id).glob("*.json")):
            yield from json.loads(p.read_text()).items()
    else:
        legacy = site_dir(site_id) / "docs.json"
        if legacy.exists():
            yield from json.loads(legacy.read_text()).items()


def load(site_id: str, name: str) -> dict:
    p = site_dir(site_id) / f"{name}.json"
    if not p.exists():
        raise _unknown_site(site_id)
    return json.loads(p.read_text())
