"""Extractor metadata to Knovas document fields (spec section 3.5).

An admin opts a source into metadata items; each item maps one value the
extractor already read to one registry key of the ``core`` pack:

    language         -> language       pdf/docx/md document properties
                                       (dc:language, often the authoring
                                       tool's locale); .eml Content-Language;
                                       never .msg
    email_date       -> document_date  .eml/.msg only: the Date header
    email_doc_type   -> doc_type       .eml/.msg only: correspondence.email
    email_author     -> author         .eml/.msg From: display name, else
                                       the address
    document_author  -> author         pdf/docx/md author, junk skipped

Never mapped: the file mtime, the Microsoft 365 lastModifiedDateTime and
the PDF/DOCX/MD created/modified dates never become ``document_date`` (a
document's creation date is the template's or the editor's date, not the
date of the deed); no Message-ID (it would flood the human-facing
``reference`` field); no sender/recipient keys (no pack has them, and a
mapped key is never auto-registered). The input of ``map_metadata`` is the
extractor's ``source_metadata`` only, so a file-system or M365 date cannot
reach it.

Values are customer data: nothing in this module logs.
"""
from __future__ import annotations

import re
from email.utils import getaddresses
from typing import Any, Iterable, Mapping, Optional

#: Bumped whenever a rule below changes what an item yields; it is part of
#: the config digest, so a bump re-sends every source that uses metadata.
METADATA_MAPPING_VERSION = 1

METADATA_ITEMS = ("language", "email_date", "email_doc_type", "email_author", "document_author")

#: The registry key each item writes.
ITEM_TARGETS = {
    "language": "language",
    "email_date": "document_date",
    "email_doc_type": "doc_type",
    "email_author": "author",
    "document_author": "author",
}

EMAIL_DOC_TYPE = "correspondence.email"

EMAIL_EXTENSIONS = frozenset({".eml", ".msg"})
DOCUMENT_EXTENSIONS = frozenset({".pdf", ".docx", ".md"})

#: Keys of ``ExtractedDocument.source_metadata`` (knovas-extract ``Metadata``
#: attributes, plus the .eml Content-Language header from ``extra``).
SOURCE_METADATA_ATTRIBUTES = ("author", "language", "created", "modified")
EML_CONTENT_LANGUAGE = "eml:content_language"

LANGUAGE_RE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$")
_SKIPPED_LANGUAGES = frozenset({"x-default", "und"})

#: Placeholder authors that office tools write by default, compared casefolded.
JUNK_AUTHORS = frozenset(
    {"administrator", "admin", "user", "owner", "unknown", "author", "microsoft office user"}
)


def source_metadata_from(metadata: Any) -> dict[str, str]:
    """The extractor values the mapping may use, as plain strings.

    Reads ``author``, ``language``, ``created`` and ``modified`` from a
    knovas-extract ``Metadata`` and ``extra["eml:content_language"]``.
    Missing, non-string and blank values are left out; values are stripped
    but otherwise kept verbatim.
    """
    out: dict[str, str] = {}
    if metadata is None:
        return out
    for name in SOURCE_METADATA_ATTRIBUTES:
        value = getattr(metadata, name, None)
        if isinstance(value, str) and value.strip():
            out[name] = value.strip()
    extra = getattr(metadata, "extra", None)
    if isinstance(extra, dict):
        value = extra.get(EML_CONTENT_LANGUAGE)
        if isinstance(value, str) and value.strip():
            out[EML_CONTENT_LANGUAGE] = value.strip()
    return out


def _normalize_ext(ext: str) -> str:
    ext = str(ext or "").strip().lower()
    if ext and not ext.startswith("."):
        ext = "." + ext
    return ext


def _text(md: Mapping[str, Any], key: str) -> Optional[str]:
    value = md.get(key)
    if isinstance(value, str):
        value = value.strip()
        if value:
            return value
    return None


def _language(md: Mapping[str, Any], ext: str) -> Optional[str]:
    if ext in DOCUMENT_EXTENSIONS:
        value = _text(md, "language")
    elif ext == ".eml":
        value = _text(md, EML_CONTENT_LANGUAGE)
    else:
        return None
    if value is None or not LANGUAGE_RE.match(value):
        return None
    if value.casefold() in _SKIPPED_LANGUAGES or value.split("-", 1)[0].casefold() == "und":
        return None
    return value


def _email_author(raw: Optional[str]) -> Optional[str]:
    """From: the display name, else the address. A bare name without an
    address is the name itself (``parseaddr`` would mangle it)."""
    if raw is None:
        return None
    if "@" not in raw and "<" not in raw:
        name = raw.strip().strip('"').strip()
        return name or None
    for name, address in getaddresses([raw]):
        name = (name or "").strip()
        address = (address or "").strip()
        if name or address:
            return name or address
    return None


def _document_author(raw: Optional[str]) -> Optional[str]:
    if raw is None or raw.casefold() in JUNK_AUTHORS:
        return None
    return raw


def map_metadata(md: Optional[Mapping[str, Any]], ext: str, enabled: Iterable[str]) -> dict[str, Any]:
    """The field values the enabled items yield for one document.

    ``md`` is ``ExtractedDocument.source_metadata``; ``ext`` the file
    extension (``.eml`` or ``eml``, any case). Items that do not apply to
    the extension, and values that fail their rule, yield nothing. Unknown
    item names are ignored (the sync schema already refuses them).
    """
    items = frozenset(enabled or ())
    if not items:
        return {}
    md = md or {}
    ext = _normalize_ext(ext)
    email = ext in EMAIL_EXTENSIONS
    document = ext in DOCUMENT_EXTENSIONS
    out: dict[str, Any] = {}

    if "language" in items:
        language = _language(md, ext)
        if language is not None:
            out[ITEM_TARGETS["language"]] = language
    if email and "email_date" in items:
        # The Date header, verbatim; the server reads its date part. Never
        # ``modified``, never a file or M365 date.
        created = _text(md, "created")
        if created is not None:
            out[ITEM_TARGETS["email_date"]] = created
    if email and "email_doc_type" in items:
        out[ITEM_TARGETS["email_doc_type"]] = EMAIL_DOC_TYPE
    if email and "email_author" in items:
        author = _email_author(_text(md, "author"))
        if author is not None:
            out[ITEM_TARGETS["email_author"]] = author
    if document and "document_author" in items:
        author = _document_author(_text(md, "author"))
        if author is not None:
            out[ITEM_TARGETS["document_author"]] = author
    return out
