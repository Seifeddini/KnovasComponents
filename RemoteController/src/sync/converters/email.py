"""Convert .eml and .msg email files to Markdown-ish text."""
from __future__ import annotations

import email
import re
from email import policy
from html.parser import HTMLParser


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self._parts.append(text)

    def get_text(self) -> str:
        return "\n".join(self._parts)


def _html_to_text(html: str) -> str:
    parser = _HTMLTextExtractor()
    try:
        parser.feed(html)
    except Exception:
        return re.sub(r"<[^>]+>", " ", html)
    return parser.get_text()


def _decode_payload(part: email.message.Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        raw = part.get_payload()
        return raw if isinstance(raw, str) else ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return payload.decode("utf-8", errors="replace")


def _body_from_message(msg: email.message.Message) -> str:
    plain_parts: list[str] = []
    html_parts: list[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            disposition = str(part.get("Content-Disposition", ""))
            if "attachment" in disposition.lower():
                continue
            ctype = part.get_content_type()
            if ctype == "text/plain":
                plain_parts.append(_decode_payload(part).strip())
            elif ctype == "text/html":
                html_parts.append(_decode_payload(part).strip())
    else:
        ctype = msg.get_content_type()
        body = _decode_payload(msg).strip()
        if ctype == "text/html":
            html_parts.append(body)
        else:
            plain_parts.append(body)

    if plain_parts:
        return "\n\n".join(p for p in plain_parts if p)
    if html_parts:
        return "\n\n".join(_html_to_text(h) for h in html_parts if h)
    return ""


def eml_bytes_to_markdown(raw_bytes: bytes) -> str:
    msg = email.message_from_bytes(raw_bytes, policy=policy.default)
    subject = (msg.get("Subject") or "").strip()
    from_hdr = (msg.get("From") or "").strip()
    date_hdr = (msg.get("Date") or "").strip()
    lines: list[str] = []
    if subject:
        lines.append(f"# {subject}")
    meta: list[str] = []
    if from_hdr:
        meta.append(f"From: {from_hdr}")
    if date_hdr:
        meta.append(f"Date: {date_hdr}")
    if meta:
        lines.append("\n".join(meta))
    body = _body_from_message(msg)
    if body:
        lines.append(body)
    return "\n\n".join(lines).strip()


def msg_bytes_to_markdown(raw_bytes: bytes) -> str:
    try:
        import extract_msg  # type: ignore[import]
    except ImportError as exc:
        raise ImportError("extract-msg is required for .msg conversion") from exc

    import io

    msg = extract_msg.Message(io.BytesIO(raw_bytes))
    try:
        subject = (msg.subject or "").strip()
        sender = (msg.sender or "").strip()
        date_str = str(msg.date) if msg.date else ""
        lines: list[str] = []
        if subject:
            lines.append(f"# {subject}")
        meta: list[str] = []
        if sender:
            meta.append(f"From: {sender}")
        if date_str:
            meta.append(f"Date: {date_str}")
        if meta:
            lines.append("\n".join(meta))
        body = (msg.body or "").strip()
        if body:
            lines.append(body)
        return "\n\n".join(lines).strip()
    finally:
        msg.close()
