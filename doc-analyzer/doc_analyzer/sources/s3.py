"""Amazon S3 source.  `location` is  s3://bucket/prefix  or  bucket/prefix."""
from __future__ import annotations

from pathlib import Path


def fetch(session, cfg, location: str, options: dict) -> Path:
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError("the 'boto3' package is required for the S3 source "
                           "(setup.ps1 -Full)") from exc
    ver = session.ledger.record_module("boto3", "download objects from Amazon S3")
    session.detail(f"boto3: {ver}")

    loc = location.replace("s3://", "", 1).strip("/")
    if "/" in loc:
        bucket, prefix = loc.split("/", 1)
    else:
        bucket, prefix = loc, ""
    if not bucket:
        raise ValueError("no S3 bucket in location")

    kw = {}
    region = options.get("region") or cfg.get("sources.s3.region", "")
    ak = options.get("access_key_id") or cfg.get("sources.s3.access_key_id", "")
    sk = options.get("secret_access_key") or cfg.get("sources.s3.secret_access_key", "")
    if region:
        kw["region_name"] = region
    if ak and sk:
        kw["aws_access_key_id"] = ak
        kw["aws_secret_access_key"] = sk
    s3 = boto3.client("s3", **kw)

    dest = session.fetch_dir / "s3"
    dest.mkdir(parents=True, exist_ok=True)
    paginator = s3.get_paginator("list_objects_v2")
    n = 0
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/"):
                continue
            rel = key[len(prefix):].lstrip("/") if prefix and key.startswith(prefix) else key
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(bucket, key, str(target))
            session.detail(f"  s3://{bucket}/{key} -> {rel} ({obj['Size']} bytes)")
            n += 1
    if n == 0:
        raise RuntimeError(f"no objects under s3://{bucket}/{prefix}")
    session.detail(f"Downloaded {n} object(s) to {dest}")
    session.ledger.record("source", "Amazon S3", detail=f"s3://{bucket}/{prefix}",
                          purpose="download documents to analyze")
    return dest
