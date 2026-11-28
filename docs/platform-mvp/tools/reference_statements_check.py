"""Throwaway reference for the coordinator's frozen tests (second design). Not product code."""
import json
import re
import unicodedata

MAX_STATEMENT = 500
_APOS = "\u2019\u2018`\u00b4"
_QUOTES = "\u201e\u201c\u201d\u00ab\u00bb\u2039\u203a"
_DASHES = "\u2013\u2014\u2212"
_SKIP = "\u00ad\u200b\u2060\ufeff"
_NUMBER = re.compile(r"\d(?:[\d'.,]*\d)?[a-z]?(?![a-z])")
_REF = re.compile(r"\s*\[\d+(?:\s*,\s*\d+)*\]")
_LIST_MARK = re.compile(r"^(?:[-*\u2022]\s+|\d+[.)]\s+)")
WORDS = {1: ("ein", "eine", "einen", "einem", "einer", "eines", "eins"), 2: ("zwei",), 3: ("drei",), 4: ("vier",),
         5: ("fuenf", "f\u00fcnf"), 6: ("sechs",), 7: ("sieben",), 8: ("acht",), 9: ("neun",), 10: ("zehn",),
         11: ("elf",), 12: ("zwoelf", "zw\u00f6lf")}


def normalise(text):
    """(normalised text, for each of its characters the index of the original character)."""
    out, where = [], []
    for i, ch in enumerate(text):
        if ch in _SKIP:
            continue
        for c in unicodedata.normalize("NFKC", ch):
            if c in _APOS:
                c = "'"
            elif c in _QUOTES:
                c = '"'
            elif c in _DASHES:
                c = "-"
            if c.isspace():
                if out and out[-1] != " ":
                    out.append(" "); where.append(i)
                continue
            for f in c.casefold():
                out.append(f); where.append(i)
    while out and out[-1] == " ":
        out.pop(); where.pop()
    return "".join(out), where


def cut_quote(quote, passage):
    """The passage's own characters for `quote`, or None."""
    wanted = normalise(quote)[0].strip('"\' ')
    if len(wanted.split()) < 3:
        return None
    flat, where = normalise(passage)
    at = flat.find(wanted)
    while at >= 0:
        end = at + len(wanted)
        if (at == 0 or not flat[at - 1].isalnum()) and (end == len(flat) or not flat[end].isalnum()):
            return passage[where[at]:where[end - 1] + 1]
        at = flat.find(wanted, at + 1)
    return None


def numbers(text):
    flat = normalise(text)[0]
    return {m.group(0).replace("'", "") for m in _NUMBER.finditer(flat)}


def numbers_supported(statement, passage):
    have = numbers(passage)
    words = set(re.findall(r"[a-z\u00e4\u00f6\u00fc]+", normalise(passage)[0]))
    for token in numbers(statement):
        if token in have:
            continue
        if token.isdigit() and int(token) in WORDS and words & set(WORDS[int(token)]):
            continue
        return False
    return True


def _items(text):
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith("```")]
    body = "\n".join(lines).strip()
    if not body:
        return []
    try:
        whole = json.loads(body)
    except ValueError:
        whole = None
    if isinstance(whole, list):
        return whole
    if isinstance(whole, dict):
        return [whole]
    out = []
    for line in lines:
        line = _LIST_MARK.sub("", line.strip())
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            out.append(line)
    return out


def is_no_answer(text, no_answer_text):
    flat = normalise(re.sub(r"[*_`]", "", text))[0].strip('"\' .')
    return flat == normalise(no_answer_text)[0]


def check_statements(text, passages, no_answer_text, sentence_cls):
    by_number = {p.number: p for p in passages}
    kept, removed = [], 0
    for item in _items(text or ""):
        if isinstance(item, str) and is_no_answer(item, no_answer_text):
            continue
        if isinstance(item, dict) and item.get("no_answer") is True and "statement" not in item:
            continue
        ok = isinstance(item, dict)
        statement = item.get("statement") if ok else None
        source = item.get("source") if ok else None
        quote = item.get("quote") if ok else None
        if isinstance(source, str) and source.strip().isdigit():
            source = int(source.strip())
        ok = (ok and isinstance(statement, str) and statement.strip() and isinstance(quote, str) and quote.strip()
              and isinstance(source, int) and not isinstance(source, bool) and source in by_number)
        if ok:
            shown = " ".join(_REF.sub("", statement).split())
            shown = re.sub(r"\s+([.,;:!?])", r"\1", shown)
            passage = by_number[source].text
            cut = cut_quote(quote, passage)
            ok = (len(shown) <= MAX_STATEMENT and not is_no_answer(shown, no_answer_text) and cut is not None
                  and numbers_supported(shown, passage))
        if ok:
            kept.append(sentence_cls(text=shown, refs=(source,), quote=cut))
        else:
            removed += 1
    return kept, removed
