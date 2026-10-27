"""Copy the spec's blok language into an Orkestrator checkout: the spec is the source.

    python scripts/sync_standard_catalog.py ~/Code/standalones/orkestrator-next
    python scripts/sync_standard_catalog.py ~/Code/standalones/orkestrator-next --into src/renderer/src/core/blok/spec

The vocabulary (``vocabulary.json``), the spec-owned catalogs (``catalogs/base@2.json`` and the
standard catalogs ``catalogs/ui@1.json``, ``scene@1``, ``logo@1``, ``structure@1``, ``html@1``)
and the ``orkestrator`` module (``modules/orkestrator.json``, what only the Orkestrator draws)
are written into the checkout, byte for byte and at those relative paths, together with the
parity corpus (``bsx_v2.json``). Orkestrator implements them, holds its parser to the corpus,
and registers the ``orkestrator`` module (with the catalogs it implements in full).

The v1 mirror the spec still checks definitions' operations against (``declare/catalogs/
orkestrator.json``, base@1) is refreshed from a release with ``--v1-mirror <tag>`` (``gh``).
"""

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from arkitekt_spec.blok.catalogs import catalog_files, catalog_text
from arkitekt_spec.blok.corpus import write
from arkitekt_spec.blok.vocabulary import vocabulary_text

DEFAULT_INTO = Path("src/renderer/src/core/blok/spec")
REPOSITORY = "arkitektio/orkestrator"
ASSET = "blok-catalog.json"
V1_CATALOGS = Path(__file__).resolve().parent.parent / "arkitekt_spec" / "declare" / "catalogs"


def sync(checkout: Path, into: Path = DEFAULT_INTO) -> list[Path]:
    """Write the vocabulary, the catalogs and the corpus into ``checkout / into``."""
    target = checkout / into
    target.mkdir(parents=True, exist_ok=True)
    written = [target / "vocabulary.json"]
    written[0].write_text(vocabulary_text(), encoding="utf-8")
    for file in catalog_files():
        (target / file).parent.mkdir(parents=True, exist_ok=True)
        (target / file).write_text(catalog_text(file), encoding="utf-8")
        written.append(target / file)
    written.append(write(target))
    return written


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
    parser.add_argument("checkout", nargs="?", type=Path, help="an Orkestrator checkout to write the blok language into")
    parser.add_argument("--into", type=Path, default=DEFAULT_INTO, help=f"where in the checkout (default {DEFAULT_INTO})")
    parser.add_argument("--v1-mirror", metavar="TAG", help="refresh the v1 mirror (definitions) from this Orkestrator release")
    args = parser.parse_args()
    if args.v1_mirror:
        refresh_v1_mirror(args.v1_mirror)
    if args.checkout is not None:
        for path in sync(args.checkout, args.into):
            print(path)
    elif not args.v1_mirror:
        parser.error("name a checkout to sync into, or --v1-mirror <tag>")


if __name__ == "__main__":
    main()
