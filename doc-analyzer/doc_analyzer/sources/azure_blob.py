"""Azure Blob Storage source.  `location` is  container/prefix  (or a full
https://<account>.blob.core.windows.net/<container>/<prefix> URL)."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse


def fetch(session, cfg, location: str, options: dict) -> Path:
    try:
        from azure.storage.blob import ContainerClient
    except ImportError as exc:
        raise RuntimeError("the 'azure-storage-blob' package is required for the Azure "
                           "Blob source (setup.ps1 -Full)") from exc
    ver = session.ledger.record_module("azure.storage.blob", "download blobs from Azure Storage")
    session.detail(f"azure-storage-blob: {ver}")

    conn = options.get("connection_string") or cfg.get("sources.azure_blob.connection_string", "")
    account_url = options.get("account_url") or cfg.get("sources.azure_blob.account_url", "")
    sas = options.get("sas_token") or cfg.get("sources.azure_blob.sas_token", "")

    container = prefix = ""
    if location.startswith("http"):
        u = urlparse(location)
        account_url = account_url or f"{u.scheme}://{u.netloc}"
        parts = u.path.lstrip("/").split("/", 1)
        container = parts[0]
        prefix = parts[1] if len(parts) > 1 else ""
    else:
        loc = location.strip("/")
        container, _, prefix = loc.partition("/")
    if not container:
        raise ValueError("no Azure container in location")

    if conn:
        cc = ContainerClient.from_connection_string(conn, container_name=container)
    elif account_url and sas:
        cc = ContainerClient(account_url=account_url, container_name=container, credential=sas)
    else:
        raise RuntimeError("provide either a connection_string, or account_url + sas_token "
                           "(config.toml [sources.azure_blob]).")

    dest = session.fetch_dir / "azure_blob"
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    for blob in cc.list_blobs(name_starts_with=prefix or None):
        if blob.name.endswith("/"):
            continue
        rel = blob.name[len(prefix):].lstrip("/") if prefix and blob.name.startswith(prefix) else blob.name
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "wb") as fh:
            fh.write(cc.download_blob(blob.name).readall())
        session.detail(f"  {blob.name} -> {rel} ({blob.size} bytes)")
        n += 1
    if n == 0:
        raise RuntimeError(f"no blobs under {container}/{prefix}")
    session.detail(f"Downloaded {n} blob(s) to {dest}")
    session.ledger.record("source", "Azure Blob Storage", detail=f"{container}/{prefix}",
                          purpose="download documents to analyze")
    return dest
