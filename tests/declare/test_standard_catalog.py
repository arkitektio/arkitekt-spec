"""The vendored v1 catalog of an Orkestrator release: what definitions' operations are checked against.

Bloks are checked against the spec's own catalogs since blok language v2 (``tests/blok``); this
copy stays for the port validators and effects of definitions, which still speak base@1.
"""

import json
import re
from importlib import resources
from pathlib import Path

import pytest

from arkitekt_spec.declare.catalogs import (
    STANDARD_CATALOG_NAME,
    VALUE_KINDS,
    base_operations,
    load_standard_catalog,
    standard_catalog_release,
    standard_view,
    ui_catalog_view,
)

VENDORED = resources.files("arkitekt_spec.declare.catalogs").joinpath("orkestrator.json")

# Where a checkout of the app writes the catalog it would publish (`pnpm catalog:export`).
APP_EXPORT = Path(__file__).parents[4] / "standalones" / "orkestrator-next" / "dist" / "catalog" / "blok-catalog.json"


# -- the file ---------------------------------------------------------------------


def test_the_standard_catalog_loads_and_says_where_it_is_from() -> None:
    catalog = load_standard_catalog()

    assert catalog.name == STANDARD_CATALOG_NAME == "orkestrator"
    assert len(catalog.components) > 100
    assert re.fullmatch(r"v\d+\.\d+\.\d+", standard_catalog_release())


def test_it_has_the_components_a_panel_is_made_of() -> None:
    components = {component.name: component for component in load_standard_catalog().components}

    for name in ("Card", "CardHeader", "CardTitle", "CardContent", "Button", "Slider", "Progress", "Badge", "div", "span"):
        assert name in components, name
    assert components["Button"].prop("onClick").kind == "CALLBACK"  # type: ignore[union-attr]
    assert components["Slider"].prop("bind") is not None
    assert components["Card"].accepts_children and not components["Progress"].accepts_children


def test_every_prop_is_of_a_kind_there_is() -> None:
    for component in load_standard_catalog().components:
        keys = [prop.key for prop in component.props]
        assert len(keys) == len(set(keys)), component.name
        assert {prop.kind for prop in component.props} <= set(VALUE_KINDS), component.name


def test_it_only_extends_the_base_catalog() -> None:
    """The published file lists the base operations too; the catalog read from it does not."""
    published = {operation["name"] for operation in json.loads(VENDORED.read_text(encoding="utf-8"))["operations"]}
    extends = {operation.name for operation in load_standard_catalog().operations}

    assert published & set(base_operations()), "the published file no longer repeats the base: drop this test"
    assert not extends & set(base_operations())
    assert extends == published - set(base_operations())
    # ...and a view still has both.
    assert {"eq", "logic.or", "math.round"} <= set(standard_view().operations)


def test_the_vendored_file_is_the_one_the_app_exports() -> None:
    """Against a checkout of the app that has exported its catalog: a diff is a file to sync."""
    if not APP_EXPORT.is_file():
        pytest.skip("no exported catalog in a checkout of the app next to the packages")
    assert json.loads(VENDORED.read_text(encoding="utf-8")) == json.loads(APP_EXPORT.read_text(encoding="utf-8")), (
        "the app's catalog moved: release it, then run scripts/sync_standard_catalog.py"
    )


def test_an_exported_catalog_reads_as_the_vendored_one_does() -> None:
    """``ui_catalog_view`` of the vendored file is the standard view: one reading, two sources."""
    view = ui_catalog_view(json.loads(VENDORED.read_text(encoding="utf-8")))
    standard = standard_view()

    assert dict(view.components or {}) == dict(standard.components or {})
    assert dict(view.operations) == dict(standard.operations)
    assert view.authoritative


def test_a_payload_that_is_no_catalog_is_a_value_error() -> None:
    with pytest.raises(ValueError):
        ui_catalog_view({"components": [{"props": []}]})
