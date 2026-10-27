"""3D bloks: a scene's root, its vocabulary's placement, assets, models decorated by html bloks."""

from pathlib import Path

import pytest

from arkitekt_spec.blok.vocabulary import BlokError
from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.declare.blok.registry import build_declared_bloks

REF = "sha256:" + "9f" * 32
SRC = "{self.assets.scope}"


def _scope() -> AppRegistry:
    registry = AppRegistry(root=Path(__file__).parents[1] / "fixtures")
    registry.register_asset("scope", "scope.glb")

    @registry.state
    class Stage:
        x: float
        target_x: float
        speed: float
        mounted: bool

    @registry.register
    def move_joint(name: str, position: float) -> None:
        """Move one joint."""

    return registry


SCOPE = f"""
<Scene id="scope-view" camera="front" persist>
  <Model id="scope" src={SRC}>
    <Move node="stage_x" axis="x" value={{self.Stage.x}} animateTo={{self.Stage.target_x}} speed={{self.Stage.speed}} />
    <Visible node="incubator" on={{self.Stage.mounted}} />
    <Popout node="laser_out" trigger="hover">
      <Card><CardContent><Badge>Laser</Badge></CardContent></Card>
    </Popout>
  </Model>
</Scene>
"""


def test_a_scene_blok_registers_and_carries_its_context_and_default() -> None:
    registry = _scope()
    registry.register_blok("scope_3d", SCOPE, default=True, mode="strict")
    registry.register_blok("turret_panel", "<Html><Card /></Html>", default=True, for_model=REF, for_node="turret")

    declared = build_declared_bloks(registry)
    wire = declared["scope_3d"].model_dump(mode="json", by_alias=True, exclude_none=True)
    assert (wire["context"], wire["default"], wire["catalogs"]) == ("SCENE", True, ["scene@1", "ui@1"])
    panel = declared["turret_panel"].model_dump(mode="json", by_alias=True, exclude_none=True)
    assert (panel["context"], panel["forModel"], panel["forNode"]) == ("HTML", REF, "turret")


@pytest.mark.parametrize(
    ("tree", "options", "code"),
    [
        ("<Card />", {}, "context.root_unknown"),
        (f"<Card>{SCOPE}</Card>", {}, "context.root_unknown"),
        (f"<Html><Model src={SRC} /></Html>", {}, "catalog.placement"),
        (f'<Scene><Model src="{REF}" /></Scene>', {}, "catalog.asset"),
        ("<Scene><Model src={self.Stage.x} /></Scene>", {}, "catalog.asset"),
        ('<Scene><Move node="a" axis="x" /></Scene>', {}, "catalog.placement"),
        (f"<Scene><Model src={SRC}><Badge /></Model></Scene>", {}, "catalog.placement"),
    ],
)
def test_a_scene_blok_that_breaks_the_rules_is_refused(tree: str, options: dict, code: str) -> None:
    with pytest.raises(BlokError) as caught:
        _scope().register_blok("bad", tree, **options, mode="strict")
    assert caught.value.code == code


@pytest.mark.parametrize(
    ("tree", "options", "message"),
    [
        ("<Html><Card /></Html>", {"for_model": "9f2c"}, "for_model must be 'sha256:'"),
        ("<Html><Card /></Html>", {"for_node": "turret"}, "for_node without a for_model"),
        (SCOPE, {"for_model": REF}, "only an html blok can decorate a model"),
    ],
)
def test_a_decoration_names_a_model(tree: str, options: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _scope().register_blok("bad", tree, **options)


def test_a_handler_reading_the_event_declares_no_dependency_for_it() -> None:
    registry = _scope()
    registry.register_blok(
        "arm_3d",
        f"<Scene><Robot src={SRC} onJointChange={{self.move_joint(name: event.name, position: event.value)}} /></Scene>",
        mode="strict",
    )
    (dependency,) = build_declared_bloks(registry)["arm_3d"].dependencies
    assert [demand.key for demand in dependency.action_dependencies or ()] == ["move_joint"]


def test_an_event_field_the_component_does_not_hand_is_refused() -> None:
    with pytest.raises(BlokError, match="callback.event_field"):
        _scope().register_blok(
            "arm_3d", f"<Scene><Robot src={SRC} onJointChange={{self.move_joint(name: event.joint, position: 1)}} /></Scene>"
        )
