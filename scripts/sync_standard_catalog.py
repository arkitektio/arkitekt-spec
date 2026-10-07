"""Copy the standard blok catalog from an Orkestrator release into this package.

    python scripts/sync_standard_catalog.py            # the latest release
    python scripts/sync_standard_catalog.py v2.19.0    # that one

The catalog is the frontend's to write: ``blok-catalog.json`` is attached to every
release of arkitektio/orkestrator, and this is the only way the copy here changes.
It needs the GitHub CLI (``gh``), logged in. Run the tests afterwards: a blok that
used something the new catalog dropped fails there.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPOSITORY = "arkitektio/orkestrator"
ASSET = "blok-catalog.json"
CATALOGS = Path(__file__).resolve().parent.parent / "arkitekt_spec" / "declare" / "catalogs"


def latest_release() -> str:
    """The tag of the newest release."""
    result = subprocess.run(
        ["gh", "release", "view", "-R", REPOSITORY, "--json", "tagName", "-q", ".tagName"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def sync(tag: str) -> None:
    """Download the catalog of release ``tag`` and write it, and the tag, into the package."""
    with tempfile.TemporaryDirectory() as directory:
        subprocess.run(
            ["gh", "release", "download", tag, "-R", REPOSITORY, "-p", ASSET, "-D", directory],
            check=True,
        )
        text = (Path(directory) / ASSET).read_text(encoding="utf-8")
    catalog = json.loads(text)  # refuses a download that is not the catalog
    (CATALOGS / "orkestrator.json").write_text(text, encoding="utf-8")
    (CATALOGS / "orkestrator.release").write_text(tag + "\n", encoding="utf-8")
    print(
        f"{catalog['name']} of {tag}: {len(catalog['components'])} components, "
        f"{len(catalog['operations'])} operations"
    )


if __name__ == "__main__":
    sync(sys.argv[1] if len(sys.argv) > 1 else latest_release())
