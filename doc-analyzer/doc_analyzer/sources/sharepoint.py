"""SharePoint / OneDrive via Microsoft Graph, client-credentials flow.

Only 'requests' is needed. Configure tenant_id / client_id / client_secret / site
in config.toml (or the app's Options dialog). `location` is the document-library
folder path, e.g.  Shared Documents/Contracts/2026
"""
from __future__ import annotations

from pathlib import Path


def _requests(session):
    try:
        import requests
        session.ledger.record_module("requests", "call Microsoft Graph for SharePoint files")
        return requests
    except ImportError as exc:
        raise RuntimeError("the 'requests' package is required for the SharePoint source "
                           "(setup.ps1 -Full)") from exc


def _token(requests, session, cfg, options):
    tenant = options.get("tenant_id") or cfg.get("sources.sharepoint.tenant_id", "")
    client = options.get("client_id") or cfg.get("sources.sharepoint.client_id", "")
    secret = options.get("client_secret") or cfg.get("sources.sharepoint.client_secret", "")
    if not (tenant and client and secret):
        raise RuntimeError("SharePoint needs tenant_id, client_id and client_secret "
                           "(config.toml [sources.sharepoint] or the Options dialog).")
    url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    data = {"client_id": client, "client_secret": secret,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials"}
    r = requests.post(url, data=data, timeout=30)
    r.raise_for_status()
    session.ledger.record("service", "Microsoft Graph API", version="v1.0",
                          purpose="list + download SharePoint/OneDrive documents")
    return r.json()["access_token"]


def _site_id(requests, session, cfg, options, headers):
    site = options.get("site") or cfg.get("sources.sharepoint.site", "")
    if not site:
        raise RuntimeError("set [sources.sharepoint].site, e.g. "
                           "contoso.sharepoint.com:/sites/Finance:")
    r = requests.get(f"https://graph.microsoft.com/v1.0/sites/{site}", headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()["id"]


def fetch(session, cfg, location: str, options: dict) -> Path:
    requests = _requests(session)
    token = _token(requests, session, cfg, options)
    headers = {"Authorization": f"Bearer {token}"}
    site_id = _site_id(requests, session, cfg, options, headers)

    base = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/root"
    folder = (location or "").strip("/")
    listing_url = (f"{base}:/{folder}:/children" if folder else f"{base}/children")

    dest = session.fetch_dir / "sharepoint"
    dest.mkdir(parents=True, exist_ok=True)
    count = _walk(requests, session, headers, listing_url, dest)
    if count == 0:
        raise RuntimeError("no files retrieved from SharePoint (check the folder path "
                           "and that the app registration has Files.Read.All).")
    session.detail(f"Retrieved {count} file(s) from SharePoint to {dest}")
    session.ledger.record("source", "SharePoint / OneDrive", detail=location or "(drive root)",
                          purpose="download documents to analyze")
    return dest


def _walk(requests, session, headers, url, dest: Path, depth: int = 0) -> int:
    if depth > 8:
        return 0
    r = requests.get(url, headers=headers, timeout=60)
    r.raise_for_status()
    payload = r.json()
    n = 0
    for item in payload.get("value", []):
        if "folder" in item:
            sub = dest / item["name"]
            sub.mkdir(exist_ok=True)
            child_url = (f"https://graph.microsoft.com/v1.0/sites/"
                         f"{item['parentReference']['siteId']}/drive/items/{item['id']}/children")
            n += _walk(requests, session, headers, child_url, sub, depth + 1)
        elif "file" in item:
            dl = item.get("@microsoft.graph.downloadUrl")
            if not dl:
                continue
            target = dest / item["name"]
            with requests.get(dl, timeout=120, stream=True) as fr:
                fr.raise_for_status()
                with open(target, "wb") as fh:
                    for chunk in fr.iter_content(1024 * 64):
                        fh.write(chunk)
            session.detail(f"  saved {target.name} ({item.get('size', 0)} bytes)")
            n += 1
    nxt = payload.get("@odata.nextLink")
    if nxt:
        n += _walk(requests, session, headers, nxt, dest, depth)
    return n
