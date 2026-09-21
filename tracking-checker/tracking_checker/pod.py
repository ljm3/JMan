"""Save proof-of-delivery files for delivered packages."""
from __future__ import annotations

import base64
import html
import re
from datetime import datetime
from pathlib import Path

from . import models as M


def _ext(data: bytes) -> tuple[str, str]:
    if data[:4] == b"%PDF":
        return ".pdf", "application/pdf"
    if data[:4] == b"GIF8":
        return ".gif", "image/gif"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png", "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg", "image/jpeg"
    if data[:2] in (b"II", b"MM"):
        return ".tif", "image/tiff"
    head = data[:200].lstrip().lower()
    if head.startswith(b"<"):
        return ".html", "text/html"
    return ".bin", "application/octet-stream"


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", s)


def save_pod(result: M.TrackingResult, folder: Path) -> list[Path]:
    """Write every POD artefact for a delivered result; return paths, best first.

    Order of preference for the clickable link: carrier POD document, then the summary page
    (which embeds the signature and delivery photo when the carrier provided them).
    """
    pod = result.pod
    if pod is None or not result.delivered:
        return []
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{result.carrier}_{_safe(result.tracking_number)}"
    saved: list[Path] = []
    doc_path = sig_path = photo_path = page_path = None
    if pod.page_capture:
        ext, _ = _ext(pod.page_capture)
        page_path = folder / f"{stem}_carrier_page{ext}"
        page_path.write_bytes(pod.page_capture)

    if pod.document:
        ext, _ = _ext(pod.document)
        doc_path = folder / f"{stem}_POD{ext}"
        doc_path.write_bytes(pod.document)
    if pod.signature_image:
        ext, _ = _ext(pod.signature_image)
        sig_path = folder / f"{stem}_signature{ext}"
        sig_path.write_bytes(pod.signature_image)
    if pod.photo:
        ext, _ = _ext(pod.photo)
        photo_path = folder / f"{stem}_delivery_photo{ext}"
        photo_path.write_bytes(pod.photo)

    summary = folder / f"{stem}_POD_summary.html"
    summary.write_text(summary_html(result, doc_path, sig_path, photo_path, page_path), encoding="utf-8")

    if doc_path:
        saved.append(doc_path)
        pod.kind = pod.document_label or "Carrier POD document"
    elif sig_path or photo_path:
        saved.append(summary)
        pod.kind = "POD summary with " + " and ".join(
            x for x, p in (("signature image", sig_path), ("delivery photo", photo_path)) if p)
    elif page_path:
        saved.append(page_path)
        pod.kind = f"Capture of the {result.carrier} tracking page (no signature shown)"
    else:
        saved.append(summary)
        pod.kind = "POD summary (carrier gave no signature image)"
    for p in (summary, page_path, sig_path, photo_path):
        if p and p not in saved:
            saved.append(p)
    pod.files = [str(p) for p in saved]
    return saved


def _img_tag(data: bytes | None, alt: str) -> str:
    if not data:
        return ""
    _, mime = _ext(data)
    if not mime.startswith("image/") or mime == "image/tiff":
        return ""
    return f'<img alt="{html.escape(alt)}" src="data:{mime};base64,{base64.b64encode(data).decode()}">'


def summary_html(r: M.TrackingResult, doc: Path | None, sig: Path | None, photo: Path | None,
                 page: Path | None = None) -> str:
    e = html.escape
    pod = r.pod or M.ProofOfDelivery()
    delivered = r.delivered_at.strftime("%A, %B %d, %Y at %I:%M %p") if r.delivered_at else "Not reported"
    demo = "DEMO" in (r.source or "")
    rows = [("Tracking number", r.tracking_number), ("Carrier", r.carrier), ("Service", r.service),
            ("Status", r.status_detail or r.status), ("Delivered", delivered),
            ("Signed for by", pod.signed_by or "No signature name reported"),
            ("Left at / delivery location", pod.left_at or "Not reported"),
            ("Delivery address", pod.address or r.destination or "Not reported"),
            ("Shipped from", r.origin), ("Ship date", r.ship_date.strftime("%Y-%m-%d") if r.ship_date else ""),
            ("Weight", r.weight), ("Data source", r.source)]
    table = "".join(f"<tr><th>{e(k)}</th><td>{e(str(v or ''))}</td></tr>" for k, v in rows)
    links = []
    if doc:
        links.append(f'<a href="{e(doc.name)}">{e(pod.document_label or "Carrier POD document")}</a>')
    if sig:
        links.append(f'<a href="{e(sig.name)}">Signature image file</a>')
    if photo:
        links.append(f'<a href="{e(photo.name)}">Delivery photo file</a>')
    if page:
        links.append(f'<a href="{e(page.name)}">Capture of the {e(r.carrier)} tracking page</a>')
    events = "".join(
        f"<tr><td>{e(ev.timestamp.strftime('%Y-%m-%d %H:%M') if ev.timestamp else '')}</td>"
        f"<td>{e(ev.description)}</td><td>{e(ev.location)}</td></tr>" for ev in r.events)
    sig_img = _img_tag(pod.signature_image, "Signature")
    photo_img = _img_tag(pod.photo, "Delivery photo")
    banner = ('<p class="demo">DEMO DATA - generated for testing, not a real delivery record.</p>' if demo else "")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Proof of Delivery - {e(r.tracking_number)}</title>
<style>
 body{{font-family:Segoe UI,Arial,sans-serif;margin:32px;color:#1f2937;max-width:900px}}
 h1{{font-size:22px;margin:0 0 4px}} .sub{{color:#6b7280;margin:0 0 20px;font-size:13px}}
 table{{border-collapse:collapse;width:100%;margin:12px 0 20px}} th,td{{border:1px solid #e5e7eb;padding:6px 10px;
 text-align:left;font-size:14px;vertical-align:top}} th{{background:#f3f4f6;width:230px}}
 .sig img,.photo img{{max-width:420px;border:1px solid #d1d5db;padding:6px;background:#fff}}
 .demo{{background:#fee2e2;color:#991b1b;padding:8px 12px;font-weight:600}}
 .links a{{margin-right:16px}} h2{{font-size:16px;margin-top:24px}}
</style></head><body>
{banner}
<h1>Proof of Delivery - {e(r.carrier)} {e(r.tracking_number)}</h1>
<p class="sub">Compiled by Tracking Check from {e(r.carrier)} tracking data on
{datetime.now().strftime('%Y-%m-%d %H:%M')}. This summary is not a carrier-issued document{'; the carrier document is linked below' if doc else ''}.</p>
<table>{table}</table>
{f'<p class="links">{" ".join(links)}</p>' if links else ''}
{f'<h2>Signature</h2><div class="sig">{sig_img}</div>' if sig_img else ''}
{f'<h2>Delivery photo</h2><div class="photo">{photo_img}</div>' if photo_img else ''}
<h2>Tracking history</h2>
<table><tr><th style="width:150px">Date / time</th><th>Event</th><th>Location</th></tr>{events}</table>
</body></html>"""
