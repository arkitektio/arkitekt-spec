"""Sync the blok language and its catalogs between the spec and the checkouts that implement them.

    python scripts/sync_standard_catalog.py ~/Code/typescript/libraries/bsx
    python scripts/sync_standard_catalog.py ~/Code/typescript/libraries/bsx --into packages/bsx/src/spec
    python scripts/sync_standard_catalog.py --orkestrator ~/Code/standalones/orkestrator-next

Two directions, one owner each:

- The **language and the base catalog** are the spec's: the vocabulary (``vocabulary.json``),
  the base catalog (``catalogs/base@4.json``: everything every renderer draws) and the parity
  corpus (``bsx_v2.json``) are written **out** into a checkout of ``@arkitektio/bsx``, whose
  contracts are held to them.
- An **app catalog** is its frontend's. The Orkestrator's
  (``https://arkitekt.live/catalogs/orkestrator/v1``) is written by the Orkestrator
  (``node scripts/export-blok-catalog.mjs`` -> ``dist/catalog/orkestrator@1.json``, which is
  also what it publishes at that address); ``--orkestrator <checkout>`` copies it **in** as
  the snapshot this package ships (``catalogs/orkestrator@1.json``), so a blok written for it
  is checked offline. The snapshot is written out to the bsx checkout with the rest.
"""

import argparse
from pathlib import Path

from arkitekt_spec.blok.catalogs import (
    ORKESTRATOR_CATALOG_ID,
    catalog_chain,
    catalog_files,
    catalog_text,
    parse_catalog,
)
from arkitekt_spec.blok.corpus import write
from arkitekt_spec.blok.vocabulary import load_vocabulary, vocabulary_text

DEFAULT_INTO = Path("packages/bsx/src/spec")
BLOK = Path(__file__).resolve().parent.parent / "arkitekt_spec" / "blok"
ORKESTRATOR_EXPORT = Path("dist") / "catalog" / "orkestrator@1.json"


def sync(checkout: Path, into: Path = DEFAULT_INTO) -> list[Path]:
    """Write the vocabulary, every catalog this package ships and the corpus out into ``checkout / into``."""
    target = checkout / into
    written: list[Path] = []
    for file in catalog_files():
        (target / file).parent.mkdir(parents=True, exist_ok=True)
        (target / file).write_text(catalog_text(file), encoding="utf-8")
        written.append(target / file)
    (target / "vocabulary.json").write_text(vocabulary_text(), encoding="utf-8")
    written += [target / "vocabulary.json", write(target)]
    return written


def sync_orkestrator_catalog(checkout: Path) -> Path:
    """Copy the Orkestrator's catalog in from an Orkestrator checkout's export.

    Raises:
        ValueError: If the export is not that catalog, or redefines a component of the base.
    """
    text = (checkout / ORKESTRATOR_EXPORT).read_text(encoding="utf-8")
    catalog = parse_catalog(text, str(ORKESTRATOR_EXPORT))
    if catalog.id != ORKESTRATOR_CATALOG_ID:
        raise ValueError(
            f"{ORKESTRATOR_EXPORT} is the catalog {catalog.id!r}, not {ORKESTRATOR_CATALOG_ID!r}"
        )
    catalog_chain(catalog)
    target = BLOK / load_vocabulary().bundled_catalogs[ORKESTRATOR_CATALOG_ID]
    target.write_text(text, encoding="utf-8")
    return target


def main() -> None:
    """Sync into a bsx checkout, or copy the Orkestrator's catalog in."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "checkout",
        nargs="?",
        type=Path,
        help="a checkout of @arkitektio/bsx (typescript/libraries/bsx): the language and the catalogs are written out into it",
    )
    parser.add_argument(
        "--into",
        type=Path,
        default=DEFAULT_INTO,
        help=f"where in the checkout (default {DEFAULT_INTO})",
    )
    parser.add_argument(
        "--orkestrator",
        type=Path,
        metavar="CHECKOUT",
        help=f"copy the Orkestrator's catalog in from this Orkestrator checkout's {ORKESTRATOR_EXPORT}",
    )
    args = parser.parse_args()
    if args.orkestrator is not None:
        print(sync_orkestrator_catalog(args.orkestrator))
    if args.checkout is not None:
        for path in sync(args.checkout, args.into):
            print(path)
    elif args.orkestrator is None:
        parser.error("name a checkout to sync into, or --orkestrator <checkout>")


if __name__ == "__main__":
    main()
