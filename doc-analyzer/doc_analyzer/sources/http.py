from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

from ..extract import SUPPORTED_EXTS

_HREF = re.compile(r'href=["\']([^"\'#?]+)["\']', re.I)


def _requests(session):
    try:
        import requests
        session.ledger.record_module("requests", "download files over HTTP(S)")
        return requests
    except ImportError as exc:
        raise RuntimeError("the 'requests' package is required for the HTTP source "
                           "(setup.ps1 -Full)") from exc


def fetch(session, cfg, location: str, options: dict) -> Path:
    if not location:
        raise ValueError("no URL was provided")
    requests = _requests(session)
    token = options.get("token") or cfg.get("sources.http.token", "")
    headers = {"User-Agent": "doc-analyzer/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    dest = session.fetch_dir / "http"
    dest.mkdir(parents=True, exist_ok=True)

    parsed = urlparse(location)
    ext = Path(parsed.path).suffix.lower()
    downloaded = 0

    if ext in SUPPORTED_EXTS:
        _download(requests, session, location, headers, dest)
        downloaded = 1
    else:
        session.detail(f"Treating {location} as an index page; scanning for document links ...")
        resp = requests.get(location, headers=headers, timeout=30)
        resp.raise_for_status()
        links = set()
        for href in _HREF.findall(resp.text):
            full = urljoin(location, href)
            lp = urlparse(full)
            if lp.netloc and lp.netloc != parsed.netloc:
                continue
            if Path(lp.path).suffix.lower() in SUPPORTED_EXTS:
                links.add(full)
        session.detail(f"Found {len(links)} downloadable document link(s).")
        for i, url in enumerate(sorted(links), 1):
            try:
                _download(requests, session, url, headers, dest)
                downloaded += 1
            except Exception as exc:  # noqa: BLE001
                session.warn(f"[{i}] {url}: {exc}")

    if downloaded == 0:
        raise RuntimeError("no documents could be downloaded from the URL")
    session.detail(f"Downloaded {downloaded} file(s) to {dest}")
    session.ledger.record("source", "HTTP(S)", detail=location, purpose="download documents")
    return dest


def _download(requests, session, url, headers, dest: Path):
    name = Path(urlparse(url).path).name or "download.bin"
    target = dest / name
    n = 1
    while target.exists():
        target = dest / f"{Path(name).stem}_{n}{Path(name).suffix}"
        n += 1
    with requests.get(url, headers=headers, timeout=60, stream=True) as r:
        r.raise_for_status()
        with open(target, "wb") as fh:
            for chunk in r.iter_content(1024 * 64):
                fh.write(chunk)
    session.detail(f"  saved {target.name} ({target.stat().st_size} bytes)")
