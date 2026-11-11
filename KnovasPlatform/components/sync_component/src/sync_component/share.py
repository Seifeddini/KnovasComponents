"""The mapping runner over a mounted file share: ``python -m sync_component.share --root <folder> plan …``.

brain-runner reads a checkout from the folder its configuration sits in, and asks git for each file's date. A share is
neither: it is mounted elsewhere, read only, and running git once per file would take days on a large one. So it gets
its own source: the files as they are, the file system's date, and no link that leads out of the share.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from brain_runner.__main__ import main as runner_main
from brain_runner.sources import LocalSource
from brain_runner.sources.base import path_order


class ShareSource(LocalSource):
    kind = "share"

    def glob(self, pattern: str) -> list[str]:
        base = self.root.resolve()
        found = []
        for path in self.root.glob(pattern):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                path.resolve().relative_to(base)      # a linked folder that leads out of the share is not the share
            except ValueError:
                continue
            found.append(path.relative_to(self.root).as_posix())
        return sorted(found, key=path_order)

    def last_commit(self, path: str):
        return None

    def tracked(self):
        return None

    def history(self, since: str, roles: dict) -> list[dict]:
        return []

    def local_dir(self, path: str):
        return None

    def describe(self) -> dict:
        return {"kind": self.kind}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", required=True)
    args, rest = ap.parse_known_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    return runner_main(rest, source=ShareSource(Path(args.root)))


if __name__ == "__main__":
    sys.exit(main())
