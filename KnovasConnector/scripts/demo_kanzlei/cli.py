#!/usr/bin/env python3
"""build / verify / upload / list / touch — same ops shape as fetch_demo_corpus.py."""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from demo_kanzlei.build_world import load_config
from demo_kanzlei.pipeline import LAYOUTS, STAGES, run_pipeline
from demo_kanzlei.verify import verify_client, verify_corpus, warn_if_stale

log = logging.getLogger("demo-kanzlei")
CONFIG_PATH = Path(__file__).with_name("world.toml")


def _work(args: argparse.Namespace) -> Path:
    work = args.work or args.out.parent / f".{args.out.name}-work"
    return work.expanduser().resolve()


def cmd_build(args: argparse.Namespace) -> int:
    work = _work(args)
    try:
        return run_pipeline(
            cfg_path=args.config,
            out_root=args.out.expanduser().resolve(),
            work_root=work,
            pilot=not args.full,
            skip_zefix=args.skip_zefix,
            until_stage=args.until_stage,
            layout=args.layout,
        )
    except Exception as exc:
        log.error("%s", exc)
        return 1


def cmd_verify(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    check = verify_client if args.layout == "client" else verify_corpus
    return check(
        args.out.expanduser().resolve(), cfg, manifest_path=_work(args) / "manifest.jsonl", skip_scan_ocr=not args.full
    )


def cmd_screen(args: argparse.Namespace) -> int:
    """Check every invented company name of the full build against the commercial register."""
    import time as _time

    from demo_kanzlei.build_world import build_world
    from demo_kanzlei.records import build_records
    from demo_kanzlei.namepool import AUTHORITIES
    from demo_kanzlei.screen_names import ZefixClient, _norm

    cfg = load_config(args.config)
    world = build_world(cfg, pilot=False, skip_zefix=True)
    records = build_records(world, cfg, pilot=False)
    names = sorted({a.firma.strip() for a in records.addresses if a.firma and a.firma not in AUTHORITIES and not a.duplicate_of})
    names = [world.firm.name, *names]
    if args.limit:
        names = names[: args.limit]
    client = ZefixClient()
    hits: list[str] = []
    for index, name in enumerate(names, start=1):
        found = client.search_exact(name)
        if any(_norm(hit) == _norm(name) for hit in found):
            hits.append(name)
            log.error("im Handelsregister: %s", name)
        if index % 50 == 0:
            log.info("%d von %d geprueft, %d Treffer", index, len(names), len(hits))
        _time.sleep(args.pause)
    log.info("%d Namen geprueft, %d Treffer", len(names), len(hits))
    return 1 if hits else 0


def cmd_list(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    print(f"seed={cfg['seed']}  seat={cfg['seat']}")
    print(f"matters={cfg['matters']['count']}  formats.long={cfg['formats']['long']}  email={cfg['formats']['email']}")
    print("tiers:")
    for key, value in cfg["tiers"].items():
        print(f"  {key:24} {value}")
    return 0


def cmd_upload(args: argparse.Namespace) -> int:
    root = args.out.expanduser().resolve()
    if not root.is_dir() or not any(root.iterdir()):
        log.error("%s ist leer — zuerst 'build' laufen lassen", root)
        return 2
    remote = args.remote_path.rstrip("/")
    if args.host:
        target = f"{args.user + '@' if args.user else ''}{args.host}:{remote}/"
    else:
        target = f"{remote}/"
    cmd = ["rsync", "-a", "--partial", "--stats", "-v", f"{root}/", target]
    if not args.execute:
        cmd.insert(1, "--dry-run")
        log.info("Probelauf (kein Schreibzugriff). Mit --execute wirklich uebertragen.")
    log.info("$ %s", " ".join(cmd))
    return subprocess.call(cmd)


def cmd_touch(args: argparse.Namespace) -> int:
    root = args.out.expanduser().resolve()
    now = time.time()
    count = 0
    for path in root.rglob("*"):
        if path.is_file():
            os_utime(path, now)
            count += 1
    log.info("touched %d files in %s", count, root)
    warn_if_stale(root)
    return 0


def os_utime(path: Path, now: float) -> None:
    import os

    os.utime(path, (now, now))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="Korpus lokal stufenweise erzeugen")
    build.add_argument("--out", type=Path, required=True)
    build.add_argument("--work", type=Path, help="world/plan/cache/ground_truth (ausserhalb des Watch-Roots)")
    build.add_argument("--full", action="store_true", help="alle Mandate; Default ist --pilot (1 Akte)")
    build.add_argument("--skip-zefix", action="store_true")
    build.add_argument("--until-stage", choices=list(STAGES))
    build.add_argument("--layout", choices=list(LAYOUTS), default="aktenplan",
                       help="aktenplan: ein Ordner je Akte. client: die Freigabe wie bei einem Kunden, "
                            "mit Datenbank, Verzeichnis und Wahrheitsdateien im Arbeitsordner")
    build.set_defaults(func=cmd_build)

    verify = sub.add_parser("verify", help="Formate, Manifest, Extraktion, Frische")
    verify.add_argument("--out", type=Path, required=True)
    verify.add_argument("--work", type=Path, help="Arbeitsordner des Builds (dort liegt das Manifest)")
    verify.add_argument("--full", action="store_true")
    verify.add_argument("--layout", choices=list(LAYOUTS), default="aktenplan")
    verify.set_defaults(func=cmd_verify)

    screen = sub.add_parser("screen", help="erfundene Firmennamen gegen das Handelsregister (Zefix) pruefen")
    screen.add_argument("--limit", type=int, default=0)
    screen.add_argument("--pause", type=float, default=0.15)
    screen.set_defaults(func=cmd_screen)

    listing = sub.add_parser("list", help="world.toml anzeigen")
    listing.set_defaults(func=cmd_list)

    upload = sub.add_parser("upload", help="Korpus per rsync kopieren")
    upload.add_argument("--out", type=Path, required=True)
    upload.add_argument("--host")
    upload.add_argument("--remote-path", required=True)
    upload.add_argument("--user")
    upload.add_argument("--execute", action="store_true")
    upload.set_defaults(func=cmd_upload)

    touch = sub.add_parser("touch", help="mtime auffrischen (30-Tage-Falle)")
    touch.add_argument("--out", type=Path, required=True)
    touch.set_defaults(func=cmd_touch)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
