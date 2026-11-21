"""Small files in formats the Knovas Connector does not read.

A client's share is mostly such files: old Word documents, scans saved as TIFF,
spreadsheets, dictations, mail archives, installers, backups. They make the
share large and slow to walk without sending anything to Knovas. Each is a
plausible file of its kind (right signature, some content), not a real one.

Every function is a pure function of its arguments: same input, same bytes.
"""
from __future__ import annotations

import random
import struct
import zipfile
from io import BytesIO

from PIL import Image, ImageDraw

#: Extensions the Connector syncs (``SYNCABLE_EXTENSIONS`` in its source).
SYNCABLE = {".md", ".txt", ".docx", ".pdf", ".eml", ".msg"}

OLE_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")
ZIP_STAMP = (2012, 1, 1, 0, 0, 0)


def noise(key: str, size: int) -> bytes:
    return random.Random(f"noise:{key}").randbytes(size)


def rtf(text: str) -> bytes:
    """A Word-readable RTF file. Many old ``.doc`` files are exactly this."""
    out = [r"{\rtf1\ansi\ansicpg1252\deff0{\fonttbl{\f0 Arial;}}\f0\fs22 "]
    for ch in text:
        if ch == "\n":
            out.append(r"\par" + "\n")
        elif ch in "\\{}":
            out.append("\\" + ch)
        elif ord(ch) < 128:
            out.append(ch)
        else:
            raw = ch.encode("cp1252", errors="replace")
            out.append("".join(f"\\'{b:02x}" for b in raw))
    out.append("}")
    return "".join(out).encode("ascii")


def wordperfect(text: str, key: str) -> bytes:
    return b"\xffWPC" + struct.pack("<I", 16) + b"\x01\x0a\x00\x01\x00\x00\x00\x00" + text.encode("cp1252", "replace") + noise(key, 256)


def ole(key: str, size: int) -> bytes:
    """A file with the signature of the old binary Office formats."""
    return OLE_MAGIC + noise(key, max(504, size - 8))


def _zip(entries: list[tuple[str, bytes]]) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries:
            info = zipfile.ZipInfo(name, date_time=ZIP_STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return buf.getvalue()


def xlsx(rows: list[list[str]]) -> bytes:
    """A minimal valid workbook with one sheet of text cells."""

    def esc(value: str) -> str:
        return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    sheet_rows = "".join(
        f'<row r="{r}">' + "".join(f'<c r="{chr(65 + c)}{r}" t="inlineStr"><is><t>{esc(v)}</t></is></c>' for c, v in enumerate(row)) + "</row>"
        for r, row in enumerate(rows, start=1)
    )
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    return _zip([
        ("[Content_Types].xml",
         b'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
         b'<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
         b'<Default Extension="xml" ContentType="application/xml"/>'
         b'<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
         b'<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
         b"</Types>"),
        ("_rels/.rels",
         f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
         f'<Relationship Id="rId1" Type="{rel}/officeDocument" Target="xl/workbook.xml"/></Relationships>'.encode()),
        ("xl/workbook.xml",
         f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="{ns}" xmlns:r="{rel}"><sheets>'
         f'<sheet name="Tabelle1" sheetId="1" r:id="rId1"/></sheets></workbook>'.encode()),
        ("xl/_rels/workbook.xml.rels",
         f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
         f'<Relationship Id="rId1" Type="{rel}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'.encode()),
        ("xl/worksheets/sheet1.xml",
         f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{ns}"><sheetData>{sheet_rows}</sheetData></worksheet>'.encode("utf-8")),
    ])


def archive(key: str) -> bytes:
    return _zip([("Beilagen/Liste.txt", f"Beilagen zu {key}\n".encode()), ("Beilagen/Scan001.tif", noise(key, 900))])


def _picture(key: str, lines: list[str], mode: str) -> Image.Image:
    rng = random.Random(f"img:{key}")
    image = Image.new(mode, (240, 340), 255 if mode == "L" else "white")
    draw = ImageDraw.Draw(image)
    y = 14
    for line in lines[:14]:
        draw.text((12, y), line[:42], fill=0 if mode == "L" else "black")
        y += 14
    # A scanner leaves specks; they also make every scan a different file.
    for _ in range(40):
        draw.point((rng.randrange(240), rng.randrange(340)), fill=0 if mode == "L" else "black")
    return image


def tiff(key: str, lines: list[str]) -> bytes:
    buf = BytesIO()
    _picture(key, lines, "L").convert("1").save(buf, format="TIFF")
    return buf.getvalue()


def jpeg(key: str, lines: list[str]) -> bytes:
    buf = BytesIO()
    _picture(key, lines, "RGB").save(buf, format="JPEG", quality=40)
    return buf.getvalue()


def dictation(key: str, size: int = 6000) -> bytes:
    return b"\x02dss" + noise(key, size)


def wav(key: str, seconds: float = 0.4) -> bytes:
    samples = int(8000 * seconds)
    data = bytes(128 + (b % 5) for b in noise(key, samples))
    return b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 8000, 8000, 1, 8) + b"data" + struct.pack("<I", len(data)) + data


def outlook_archive(key: str, size: int) -> bytes:
    return b"!BDN" + noise(key, size - 4)


def sql_backup(key: str, size: int) -> bytes:
    return b"TAPE\x00\x00\x03\x00" + noise(key, size - 8)


def program(key: str, size: int) -> bytes:
    return b"MZ\x90\x00" + noise(key, size - 4)


def html(title: str, text: str) -> bytes:
    body = text.replace("&", "&amp;").replace("<", "&lt;").replace("\n", "<br>\n")
    return f"<html><head><meta charset=\"windows-1252\"><title>{title}</title></head><body><p>{body}</p></body></html>".encode("cp1252", "replace")


def letter(typ: str, reference: str, parties: str, day: str, firm: str, key: str) -> str:
    """A few lines such a document starts with. Enough to look like one, no more."""
    rng = random.Random(f"letter:{key}")
    middle = rng.choice([
        "Wir beziehen uns auf Ihr Schreiben und nehmen dazu wie folgt Stellung.",
        "In der Beilage erhalten Sie die gewünschten Unterlagen zur Kenntnis.",
        "Wir bestätigen den Termin und bitten Sie, die genannten Belege mitzubringen.",
        "Die Frist wurde antragsgemäss erstreckt. Wir halten Sie auf dem Laufenden.",
        "Besten Dank für die Zustellung. Wir prüfen die Sache und melden uns.",
    ])
    return (
        f"{firm}\n\nAktenzeichen: {reference}\nIn Sachen: {parties}\n\n{typ.replace('-', ' ')} vom {day}\n\n"
        f"Sehr geehrte Damen und Herren\n\n{middle}\n\nFreundliche Grüsse\n"
    )


def make(ext: str, *, key: str, text: str, title: str = "") -> bytes:
    """A placeholder of the given kind. ``key`` makes each file different."""
    kind = ext.lower().lstrip(".")
    lines = text.splitlines()
    if kind in {"doc", "rtf", "dot"}:
        return rtf(text)
    if kind == "wpd":
        return wordperfect(text, key)
    if kind in {"xls", "ppt", "msi", "db"}:
        return ole(key, 3000 + len(key) * 97 % 9000)
    if kind == "xlsx":
        return xlsx([[line] for line in lines[:12]] or [[title]])
    if kind in {"pptx", "dotx", "xltx", "zip"}:
        return archive(key)
    if kind in {"tif", "tiff"}:
        return tiff(key, lines)
    if kind in {"jpg", "jpeg"}:
        return jpeg(key, lines)
    if kind in {"dss", "ds2"}:
        return dictation(key)
    if kind == "wav":
        return wav(key)
    if kind in {"htm", "html"}:
        return html(title, text)
    if kind == "pst":
        return outlook_archive(key, 180_000)
    if kind == "bak":
        return sql_backup(key, 400_000)
    if kind == "exe":
        return program(key, 120_000)
    if kind in {"csv", "log", "ini", "xml", "lnk", "tmp", "url"}:
        return text.encode("cp1252", "replace")
    raise ValueError(f"no placeholder for .{kind}")
