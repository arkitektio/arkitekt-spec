"""Sync the blok language between the spec and a checkout of `@arkitektio/bsx`.

    python scripts/sync_standard_catalog.py ~/Code/typescript/libraries/bsx
    python scripts/sync_standard_catalog.py ~/Code/typescript/libraries/bsx --into packages/bsx/src/spec

Two directions, one owner each:

- The **global catalogs** (``catalogs/ui@1.json``, ``scene@1``, ``logo@1``, ``structure@1``,
  ``html@1``, ``shadcn@1``) are defined in TypeScript, as the contracts of ``@arkitektio/bsx``
  (``packages/bsx/src/catalogs``); ``pnpm spec:export`` writes them into the checkout. They are
  copied **in** from there, byte for byte, so the spec validates bloks against what the UIs draw.
- The **language** is the spec's: the vocabulary (``vocabulary.json``), base@2
  (``catalogs/base@2.json``) and the parity corpus (``bsx_v2.json``) are written **out** into the
  checkout.

The ``orkestrator`` module (``modules/orkestrator.json``: the MikroScene and table parts) is what
only the Orkestrator draws. Orkestrator writes it (``node scripts/export-blok-catalog.mjs`` →
``dist/catalog/blok-module.json``); ``--orkestrator <checkout>`` copies it in.

The v1 mirror the spec still checks definitions' operations against (``declare/catalogs/
orkestrator.json``, base@1) is refreshed from a release with ``--v1-mirror <tag>`` (``gh``).
"""

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from arkitekt_spec.blok.catalogs import BASE_NAME, catalog_text
from arkitekt_spec.blok.corpus import write
from arkitekt_spec.blok.vocabulary import load_vocabulary, vocabulary_text

DEFAULT_INTO = Path("packages/bsx/src/spec")
REPOSITORY = "arkitektio/orkestrator"
ASSET = "blok-catalog.json"
SPEC = Path(__file__).resolve().parent.parent / "arkitekt_spec"
BLOK = SPEC / "blok"
V1_CATALOGS = SPEC / "declare" / "catalogs"


def global_catalog_files() -> list[str]:
    """The global catalogs at the versions the vocabulary names, relative to ``arkitekt_spec/blok``."""
    return [f"catalogs/{name}@{version}.json" for name, version in load_vocabulary().catalogs.items()]


def sync(checkout: Path, into: Path = DEFAULT_INTO) -> list[Path]:
    """Copy the global catalogs in from ``checkout / into``, then write the language out into it."""
    source = checkout / into
    written: list[Path] = []
    for file in global_catalog_files():
        text = (source / file).read_text(encoding="utf-8")
        json.loads(text)  # refuses a file that is not JSON
        (BLOK / file).write_text(text, encoding="utf-8")
        written.append(BLOK / file)
    base = f"catalogs/{BASE_NAME}@{load_vocabulary().base_version}.json"
    (source / base).parent.mkdir(parents=True, exist_ok=True)
    (source / base).write_text(catalog_text(base), encoding="utf-8")
    (source / "vocabulary.json").write_text(vocabulary_text(), encoding="utf-8")
    written += [source / base, source / "vocabulary.json", write(source)]
    return written


def sync_orkestrator_module(checkout: Path) -> Path:
    """Copy the ``orkestrator`` module in from an Orkestrator checkout's catalog export."""
    text = (checkout / "dist" / "catalog" / "blok-module.json").read_text(encoding="utf-8")
    if json.loads(text).get("name") != "orkestrator":
        raise ValueError("blok-module.json is not the orkestrator module")
    target = BLOK / "modules" / "orkestrator.json"
    target.write_text(text, encoding="utf-8")
    return target


def refresh_v1_mirror(tag: str) -> None:
    """Download the v1 ``blok-catalog.json`` of release ``tag`` into the spec (definitions only)."""
    with tempfile.TemporaryDirectory() as directory:
        subprocess.run(["gh", "release", "download", tag, "-R", REPOSITORY, "-p", ASSET, "-D", directory], check=True)
        text = (Path(directory) / ASSET).read_text(encoding="utf-8")
    json.loads(text)  # refuses a download that is not the catalog
    (V1_CATALOGS / "orkestrator.json").write_text(text, encoding="utf-8")
    (V1_CATALOGS / "orkestrator.release").write_text(tag + "\n", encoding="utf-8")


def main() -> None:
    """Sync into a checkout, or refresh the v1 mirror."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("checkout", nargs="?", type=Path, help="a checkout of @arkitektio/bsx (typescript/libraries/bsx): its catalogs are copied in, the language written out")
    parser.add_argument("--into", type=Path, default=DEFAULT_INTO, help=f"where in the checkout (default {DEFAULT_INTO})")
    parser.add_argument("--orkestrator", type=Path, metavar="CHECKOUT", help="copy the orkestrator module in from this Orkestrator checkout's dist/catalog/blok-module.json")
    parser.add_argument("--v1-mirror", metavar="TAG", help="refresh the v1 mirror (definitions) from this Orkestrator release")
    args = parser.parse_args()
    if args.v1_mirror:
        refresh_v1_mirror(args.v1_mirror)
    if args.orkestrator is not None:
        print(sync_orkestrator_module(args.orkestrator))
    if args.checkout is not None:
        for path in sync(args.checkout, args.into):
            print(path)
    elif not args.v1_mirror and args.orkestrator is None:
        parser.error("name a checkout to sync into, --orkestrator <checkout>, or --v1-mirror <tag>")


if __name__ == "__main__":
    main()
