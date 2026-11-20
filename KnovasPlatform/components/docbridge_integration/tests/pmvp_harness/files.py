"""Reading a truth file or a replies file, and keeping what a run writes out of the repository."""
from __future__ import annotations

from pathlib import Path
from typing import Optional


def read_text(path, what: str) -> str:
    """The text of ``path`` (UTF-8, with or without a byte order mark).

    A file in another encoding is refused by name of its role (``what``), not with the decoder's own error: that
    one carries the file's bytes.
    """
    try:
        return Path(path).read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError(f"{what}: not UTF-8 text") from None


def working_tree(start=None) -> Optional[Path]:
    """The git working tree that holds ``start`` (default: this harness); None outside a checkout.

    A worktree's ``.git`` is a file, a clone's a folder: either marks the root.
    """
    probe = Path(start or __file__).resolve()
    for parent in probe.parents:
        if (parent / ".git").exists():
            return parent
    return None


def inside_the_repository(folder, *, root: Optional[Path] = None) -> bool:
    """Whether ``folder`` is, or would be, inside the working tree this harness is run from.

    What a run writes (the judging sheet, screenshots) holds customer text or a customer's pages, and one
    ``git add`` would carry it into the history.
    """
    root = working_tree() if root is None else Path(root).resolve()
    if root is None:
        return False
    target = Path(folder).resolve()
    return target == root or root in target.parents
