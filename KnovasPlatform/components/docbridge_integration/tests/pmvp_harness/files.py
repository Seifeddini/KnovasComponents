"""Reading a truth file or a replies file, and keeping what a run writes out of every repository."""
from __future__ import annotations

from pathlib import Path


def read_text(path, what: str) -> str:
    """The text of ``path`` (UTF-8, with or without a byte order mark).

    A file in another encoding is refused by name of its role (``what``), not with the decoder's own error: that
    one carries the file's bytes.
    """
    try:
        return Path(path).read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError(f"{what}: not UTF-8 text") from None


def _plain_path(folder) -> Path:
    """``folder`` without Windows' extended-length prefix, which would hide the folders above it."""
    text = str(folder)
    if text.startswith("\\\\?\\UNC\\"):
        text = "\\\\" + text[8:]
    elif text.startswith("\\\\?\\"):
        text = text[4:]
    return Path(text)


def inside_a_work_tree(folder) -> bool:
    """Whether ``folder`` is, or would be, inside any git work tree: this one, the main checkout, another
    repository.

    What a run writes (the judging sheet, screenshots) holds customer text or a customer's pages, and one
    ``git add`` would carry it into a history. Decided by walking up from the resolved path and looking for
    ``.git``: a folder in a clone, a file in a worktree.
    """
    probe = _plain_path(folder).resolve()
    return any((parent / ".git").exists() for parent in (probe, *probe.parents))
