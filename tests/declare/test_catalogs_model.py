"""The Catalog type and how several catalogs resolve into one view."""

import pytest

from arkitekt_spec.declare.catalogs import (
    BASE_CATALOG_ID,
    BASE_CATALOG_VERSION,
    ArgumentSpec,
    Catalog,
    ComponentSpec,
    OperationSpec,
    PropSpec,
    base_only_view,
    base_operations,
    base_version_named,
    check_extension_does_not_shadow_base,
    resolve_catalogs,
)

from .catalog_cases import BOX, CLAMP, ELECTRON, SLIDER


def test_base_catalog_is_a_catalog() -> None:
    """The base manifest is not a special type any more; it is just a catalog."""
    from arkitekt_spec.declare.catalogs import load_base_catalog

    base = load_base_catalog()
    assert isinstance(base, Catalog)
    assert base.name == "base" and base.version == BASE_CATALOG_VERSION
    assert base.components == ()


def test_resolve_extends_base_without_replacing_it() -> None:
    view = resolve_catalogs([ELECTRON], authoritative=True)

    assert "clamp" in view.operations
    assert set(base_operations()) <= set(view.operations)
    assert view.label == f"{BASE_CATALOG_ID} + electron"


def test_components_are_none_until_some_catalog_registers_any() -> None:
    """The base catalog has none, so component names are unknowable until a UI app registers."""
    assert resolve_catalogs([], authoritative=True).components is None
    assert resolve_catalogs([Catalog(name="empty")], authoritative=True).components is None
    assert resolve_catalogs([ELECTRON], authoritative=True).components is not None


def test_conflicting_operation_across_catalogs_is_an_error() -> None:
    """The UI could not know which one to run."""
    other = Catalog(
        name="other",
        operations=(OperationSpec(name="clamp", returns="INT", arguments=()),),
    )
    with pytest.raises(ValueError, match="defined differently"):
        resolve_catalogs([ELECTRON, other], authoritative=True)


def test_identical_operation_across_catalogs_is_fine() -> None:
    twin = Catalog(name="twin", operations=(CLAMP,))
    view = resolve_catalogs([ELECTRON, twin], authoritative=True)
    assert view.operations["clamp"] == CLAMP


def test_conflicting_component_across_catalogs_is_an_error() -> None:
    other = Catalog(
        name="other",
        components=(ComponentSpec(name="Slider", accepts_children=True),),
    )
    with pytest.raises(ValueError, match="defined differently"):
        resolve_catalogs([ELECTRON, other], authoritative=True)


def test_identical_component_across_catalogs_is_fine() -> None:
    twin = Catalog(name="twin", components=(SLIDER, BOX))
    view = resolve_catalogs([ELECTRON, twin], authoritative=True)
    assert view.components is not None and view.components["Slider"] == SLIDER


def test_a_catalog_may_not_redefine_a_base_operation() -> None:
    shadowing = Catalog(
        name="rogue",
        operations=(OperationSpec(name="gt", returns="BOOL", arguments=()),),
    )
    with pytest.raises(ValueError, match="cannot redefine base operations"):
        check_extension_does_not_shadow_base(shadowing)


def test_a_catalog_may_add_operations() -> None:
    check_extension_does_not_shadow_base(ELECTRON)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("base", BASE_CATALOG_VERSION),
        ("base@1", 1),
        ("base@2", 2),
        ("electron", None),
        ("base@", None),
        ("base@x", None),
    ],
)
def test_base_version_named(name: str, expected: int | None) -> None:
    assert base_version_named(name) == expected


def test_base_only_view_is_not_authoritative() -> None:
    """Offline there is no way to tell a typo from an operation the UI provides."""
    view = base_only_view()
    assert view.authoritative is False and view.components is None


def test_specs_are_frozen_and_comparable() -> None:
    """Identity comparison is what the conflict rules rest on."""
    assert SLIDER == ComponentSpec(
        name="Slider",
        accepts_children=False,
        props=(
            PropSpec(key="min", kind="FLOAT", required=True),
            PropSpec(key="max", kind="FLOAT", required=False),
            PropSpec(key="onChange", kind="CALLBACK", required=False),
        ),
    )
    with pytest.raises(Exception):  # noqa: B017 -- frozen models raise pydantic or dataclass errors
        SLIDER.name = "other"  # type: ignore[misc]


def test_operation_spec_keys_are_positional_order() -> None:
    spec = OperationSpec(
        name="f",
        returns="BOOL",
        arguments=(
            ArgumentSpec(key="a", kind="ANY", required=True),
            ArgumentSpec(key="b", kind="ANY", required=False),
        ),
    )
    assert spec.keys == ("a", "b") and spec.required_keys == ("a",)
