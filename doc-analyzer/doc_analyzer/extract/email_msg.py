from __future__ import annotations

from email import policy
from email.parser import BytesParser
from pathlib import Path

from .base import ExtractResult


def extract_eml(path: Path, cfg) -> ExtractResult:
    msg = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
    body = ""
    try:
        part = msg.get_body(preferencelist=("plain", "html"))
        if part is not None:
            body = part.get_content()
            if part.get_content_type() == "text/html":
                body = _strip_html(body)
    except Exception:
        body = msg.get_payload(decode=False) if isinstance(msg.get_payload(), str) else ""
    atts = [p.get_filename() for p in msg.iter_attachments() if p.get_filename()]
    meta = {
        "from": str(msg.get("From", "")),
        "to": str(msg.get("To", "")),
        "cc": str(msg.get("Cc", "")),
        "subject": str(msg.get("Subject", "")),
        "date": str(msg.get("Date", "")),
        "message_id": str(msg.get("Message-ID", "")),
        "attachments": atts,
    }
    header_txt = f"Subject: {meta['subject']}\nFrom: {meta['from']}\nTo: {meta['to']}\nDate: {meta['date']}\n\n"
    return ExtractResult(text=header_txt + (body or ""), kind="email",
                         extractor="email (stdlib)", meta=meta)


def extract_msg(path: Path, cfg) -> ExtractResult:
    try:
        import extract_msg
    except ImportError:
        return ExtractResult(kind="email", extractor="extract-msg (missing)", ok=False,
                             error="install 'extract-msg' (setup.ps1 -Full) to read Outlook .msg files",
                             meta={"skipped": True})
    m = extract_msg.Message(str(path))
    meta = {
        "from": m.sender or "",
        "to": m.to or "",
        "cc": m.cc or "",
        "subject": m.subject or "",
        "date": str(m.date or ""),
        "attachments": [a.longFilename or a.shortFilename for a in m.attachments],
    }
    header_txt = f"Subject: {meta['subject']}\nFrom: {meta['from']}\nTo: {meta['to']}\nDate: {meta['date']}\n\n"
    body = m.body or ""
    m.close()
    return ExtractResult(text=header_txt + body, kind="email", extractor="extract-msg",
                         meta=meta, tools=[("extract_msg", "read Outlook .msg files")])


def _strip_html(html: str) -> str:
    import re
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    return re.sub(r"[ \t]+\n", "\n", re.sub(r"[ \t]{2,}", " ", text)).strip()
