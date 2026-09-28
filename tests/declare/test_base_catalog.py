"""The vendored base catalog manifest and its loader."""

import os
import subprocess
import sys
from importlib import resources
from pathlib import Path

import pytest

from arkitekt_spec.declare.catalogs import (
    BASE_CATALOG_VERSION,
    base_operation,
    base_operations,
    is_base_operation,
    load_base_catalog,
)

DEFAULT_SERVER_TREE = Path("/home/jhnnsrs/Code/deployments/next/mounts/rekuest")
SERVER_TREE = Path(os.environ.get("REKUEST_SERVER_TREE", DEFAULT_SERVER_TREE))
SERVER_MANIFEST = SERVER_TREE / "rekuest_core" / "catalogs" / "base_v1.json"


def test_base_manifest_loads_via_importlib_resources() -> None:
    """The manifest ships inside the package and parses into frozen dataclasses."""
    catalog = load_base_catalog()

    assert catalog is load_base_catalog()
    assert catalog.name == "base" and catalog.version == BASE_CATALOG_VERSION == 1
    assert resources.files("arkitekt_spec.declare.catalogs").joinpath("base_v1.json").is_file()
    gt = base_operation("gt")
    assert gt is not None and gt.keys == ("a", "b") and gt.returns == "BOOL"
    assert base_operation("len_between").required_keys == ("value", "min")
    assert is_base_operation("if") and is_base_operation("add") and not is_base_operation("math.multiply")
    assert base_operation("neg").keys == ("a",) and base_operation("div").returns == "FLOAT"
    assert {"eq", "ne", "gt", "gte", "lt", "lte", "and", "or", "not", "if", "in", "len"} <= set(base_operations())


def test_loader_does_not_import_the_generated_schema() -> None:
    """``traits`` imports the loader while the generated module is still importing, so the
    loader itself must not depend on ``rekuest.protocol`` (the package ``__init__`` does,
    which is why this is a source-level check rather than a ``sys.modules`` one)."""
    from arkitekt_spec.declare import catalogs

    imports = [line for line in Path(catalogs.__file__).read_text().splitlines() if line.startswith(("import ", "from "))]
    assert not [line for line in imports if "rekuest" in line], imports

    # and the whole import chain works from a cold interpreter
    code = "import arkitekt_spec.declare.traits.calls, arkitekt_spec.declare.catalogs as c; print(c.base_operation('gt').keys)"
    output = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip()
    assert output == "('a', 'b')"


def test_vendored_manifest_matches_server() -> None:
    """The client copy is byte-identical to the server's source of truth."""
    if not SERVER_MANIFEST.exists():
        pytest.skip("server tree not checked out next to the client")
    vendored = resources.files("arkitekt_spec.declare.catalogs").joinpath("base_v1.json").read_bytes()
    assert vendored == SERVER_MANIFEST.read_bytes()
