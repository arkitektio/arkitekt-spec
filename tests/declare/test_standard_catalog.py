"""The standard catalog: the vendored copy of what the Orkestrator app renders.

A blok that names no catalog is checked against it, so a component or prop the app
does not have is refused where the blok is declared, not found out in the user
interface. The file is the app's to write; here it is only copied
(``scripts/sync_standard_catalog.py``) and read.
"""

import json
import re
from importlib import resources
from pathlib import Path

import pytest

from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.declare.blok.registry import build_declared_bloks
from arkitekt_spec.declare.catalogs import (
    STANDARD_CATALOG_CORRECTIONS,
    STANDARD_CATALOG_NAME,
    VALUE_KINDS,
    ComponentSpec,
    base_operations,
    load_published_standard_catalog,
    load_standard_catalog,
    standard_catalog_release,
    standard_view,
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
    published = {operation["name"] for operation in json.loads(VENDORED.read_text())["operations"]}
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
    assert json.loads(VENDORED.read_text()) == json.loads(APP_EXPORT.read_text()), (
        "the app's catalog moved: release it, then run scripts/sync_standard_catalog.py"
    )


# -- the corrections ----------------------------------------------------------------


def test_every_correction_is_still_needed() -> None:
    """A correction the published catalog no longer needs is to be deleted."""
    published = {component.name: component for component in load_published_standard_catalog().components}

    for name, changes in STANDARD_CATALOG_CORRECTIONS.items():
        assert name in published, f"{name} is corrected, and no longer published"
        for field, value in changes.items():
            assert getattr(published[name], field) != value, (
                f"the published catalog now says {name}.{field} == {value!r}: delete the correction"
            )


def test_a_loop_has_a_body() -> None:
    """The one correction there is: the app renders a ``foreach``'s children."""
    registry = AppRegistry()

    @registry.state
    class Stage:
        slots: list[str]

    registry.register_blok(
        "loop", '<div><foreach items="@self.Stage.slots" let="#slot"><span text="@slot" /></foreach></div>'
    )

    assert "loop" in build_declared_bloks(registry)


# -- what a blok is held to ----------------------------------------------------------


def test_a_blok_made_of_what_the_app_renders_registers() -> None:
    registry = AppRegistry()
    registry.register_blok(
        "panel",
        """
        <Card>
            <CardHeader><CardTitle text="Hello" /></CardHeader>
            <CardContent><Progress value="40" /><Badge text="Idle" variant="outline" /></CardContent>
        </Card>
        """,
    )

    assert registry.registered_bloks["panel"].catalog is None


@pytest.mark.parametrize(
    ("tree", "message"),
    [
        ("<Slidr />", "component 'Slidr' is not registered"),
        ('<Progress vlaue="3" />', r"component 'Progress' has no props \['vlaue'\]"),
        ("<Progress><span /></Progress>", "component 'Progress' does not accept children"),
        ('<Button label="Go" onClick="not a call" />', "CALLBACK prop"),
    ],
)
def test_what_the_app_does_not_render_is_refused(tree: str, message: str) -> None:
    registry = AppRegistry()

    with pytest.raises(ValueError, match=message):
        registry.register_blok("panel", tree)

    assert "panel" not in registry.registered_bloks


def test_the_refusal_says_which_catalog() -> None:
    with pytest.raises(ValueError, match=r"base@1 \+ orkestrator"):
        AppRegistry().register_blok("panel", "<Slidr />")


def test_naming_the_standard_catalog_is_the_same_as_naming_none() -> None:
    registry = AppRegistry()
    registry.register_blok("fine", "<div />", catalog="orkestrator")

    with pytest.raises(ValueError, match="'Slidr' is not registered"):
        registry.register_blok("panel", "<Slidr />", catalog="orkestrator")
    assert registry.registered_bloks["fine"].catalog == "orkestrator"


def test_a_blok_for_another_renderer_is_not_held_to_the_standard_catalog() -> None:
    registry = AppRegistry()
    # A catalog only the server knows: nothing to check components against.
    registry.register_blok("elsewhere", "<Slidr />", catalog="somewhere-else")
    # A catalog the app declares: checked against that one, and only that one.
    registry.declare_ui_catalog("mine", components=[ComponentSpec(name="Gauge")])
    registry.register_blok("own", "<Gauge />", catalog="mine")

    with pytest.raises(ValueError, match="'Card' is not registered"):
        registry.register_blok("mixed", "<Card />", catalog="mine")
    assert set(registry.registered_bloks) == {"elsewhere", "own"}


def test_an_operation_the_app_evaluates_is_known_and_one_it_does_not_is_a_warning() -> None:
    from arkitekt_spec.declare.catalogs import CatalogWarning

    registry = AppRegistry()
    registry.register_blok("known", '<span text="@utils.math.round(value=1.234, precision=2)" />')

    with pytest.warns(CatalogWarning, match="nosuchop"):
        registry.register_blok("unknown", '<span text="@utils.nosuchop(1)" />')


def test_an_operation_of_the_app_is_called_by_keyword() -> None:
    """Only a base operation takes its arguments by position.

    The published catalog lists an operation's arguments by name, not in the order a
    call would pass them (``math.round`` is ``precision, value``), so a position says
    nothing. Refusing it is what keeps ``round(1.234, 2)`` from rounding 2.
    """
    with pytest.raises(ValueError, match=r"'math.round' does not accept arguments \['0', '1'\]"):
        AppRegistry().register_blok("panel", '<span text="@utils.math.round(1.234, 2)" />')
