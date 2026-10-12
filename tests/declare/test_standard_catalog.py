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
    """The app renders a ``foreach``'s children, and since it says so, nothing here corrects it."""
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


# -- the 3D vocabulary ---------------------------------------------------------------

REF = "sha256:" + "9f" * 32
SRC = "@self.assets.scope"


def _scope() -> AppRegistry:
    registry = AppRegistry(root=Path(__file__).parents[1] / "fixtures")
    registry.register_asset("scope", "scope.glb")

    @registry.state
    class Stage:
        x: float
        target_x: float
        speed: float
        mounted: bool

    return registry


SCOPE = f"""
<Scene>
  <Model id="scope" src="{SRC}">
    <Move node="stage_x" axis="x" value="@self.Stage.x" animateTo="@self.Stage.target_x" speed="@self.Stage.speed" />
    <Show node="incubator" when="@self.Stage.mounted" />
    <Popout node="laser_out" trigger="hover">
      <Card><CardContent><Badge text="Laser" /></CardContent></Card>
    </Popout>
  </Model>
</Scene>
"""


def test_a_scene_blok_registers_and_carries_its_context_and_default() -> None:
    registry = _scope()
    registry.register_blok("scope_3d", SCOPE, context="scene", default=True)
    registry.register_blok("turret_panel", "<Card />", default=True, for_model=REF, for_node="turret")

    declared = build_declared_bloks(registry)
    wire = declared["scope_3d"].model_dump(mode="json", by_alias=True, exclude_none=True)
    assert (wire["context"], wire["default"]) == ("SCENE", True)
    panel = declared["turret_panel"].model_dump(mode="json", by_alias=True, exclude_none=True)
    assert (panel["context"], panel["forModel"], panel["forNode"]) == ("HTML", REF, "turret")


def test_a_new_default_replaces_the_previous_one_of_its_context() -> None:
    registry = _scope()
    registry.register_blok("first", "<Card />", default=True)
    registry.register_blok("view", SCOPE, context="scene", default=True)
    registry.register_blok("second", "<Card />", default=True)

    defaults = {name for name, declaration in registry.registered_bloks.items() if declaration.default}
    assert defaults == {"view", "second"}


@pytest.mark.parametrize(
    ("tree", "options", "message"),
    [
        ("<Card />", {"context": "scene"}, "the root of a scene blok must be a 'Scene', not 'Card'"),
        (f"<Card>{SCOPE}</Card>", {"context": "scene"}, "the root of a scene blok must be a 'Scene', not 'Card'"),
        ("<Stage />", {"context": "scene", "catalog": "elsewhere"}, "the root of a scene blok must be a 'Scene', not 'Stage'"),
        (f'<Model src="{SRC}" />', {}, "is a scene component and cannot be placed in the html context"),
        (f'<Scene><Model src="{REF}" /></Scene>', {}, "is an ASSET prop and must name an asset"),
        ('<Scene><Model src="@self.Stage.x" /></Scene>', {}, "is an ASSET prop and must name an asset"),
        ('<Scene><Move node="a" axis="x" /></Scene>', {}, r"must be inside one of \['Model', 'Robot'\]"),
        (f'<Scene><Model src="{SRC}"><Badge text="x" /></Model></Scene>', {}, "is a html component and cannot be placed in the scene context"),
        ("<Card />", {"for_model": "9f2c"}, "for_model must be 'sha256:'"),
        ("<Card />", {"for_node": "turret"}, "for_node without a for_model"),
        (SCOPE, {"context": "scene", "for_model": REF}, "only an html blok can decorate a model"),
    ],
)
def test_a_scene_blok_that_breaks_the_rules_is_refused(tree: str, options: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _scope().register_blok("bad", tree, **options)


def test_a_handler_reading_the_event_declares_no_dependency_for_it() -> None:
    """``event`` is what triggered the handler, not a state of some agent."""
    registry = _scope()

    @registry.register
    def move_joint(name: str, position: float) -> None:
        """Move one joint."""

    registry.register_blok(
        "arm_3d",
        f"""<Scene><Robot src="{SRC}"
              onJointChange="@self.move_joint(name=event.name, position=event.value)" /></Scene>""",
        context="scene",
    )
    blok = build_declared_bloks(registry)["arm_3d"]
    assert [dependency.key for dependency in blok.dependencies] == ["self"]
