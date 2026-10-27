"""An app's own bloks: what they read of ``self`` becomes their one dependency, and the whole
tree is checked against it when the agent's input is built."""

import inspect
import warnings
from collections.abc import Callable
from dataclasses import dataclass

import pytest

from arkitekt_spec.blok.vocabulary import BlokError
from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.declare.blok.declarations import Local
from arkitekt_spec.declare.catalogs import CatalogWarning


@pytest.fixture
def registry() -> AppRegistry:
    """An app with a state and two actions."""
    registry = AppRegistry()

    @registry.state
    @dataclass
    class Arm:
        mode: str
        joints: list[float]

    @registry.register
    def move_to(position: int) -> bool:
        """Move.

        Returns:
            bool: Whether it arrived.
        """
        return True

    @registry.register
    def home() -> None:
        """Home."""

    return registry


def built(registry: AppRegistry, name: str = "panel"):
    return registry.get_declared_bloks()[name]


def test_what_it_reads_of_self_is_its_dependency(registry: AppRegistry) -> None:
    registry.register_blok(
        "panel",
        "<Html><Card><Text>{self.Arm.mode}</Text><Button onClick={self.move_to(position: 1)}>Go</Button><Progress value={self.tasks.home.progress} /></Card></Html>",
    )

    (dependency,) = built(registry).dependencies
    assert dependency.key == "self"
    assert [demand.key for demand in dependency.action_dependencies or ()] == ["home", "move_to"]
    assert [demand.key for demand in dependency.state_dependencies or ()] == ["Arm"]


def test_a_blok_reading_nothing_of_self_has_no_dependency(registry: AppRegistry) -> None:
    registry.register_blok("panel", "<Html><Card><Text>Hello</Text></Card></Html>")

    assert built(registry).dependencies == ()


def test_its_demo_is_a_placeholder_per_state_it_reads(registry: AppRegistry) -> None:
    registry.register_blok("panel", "<Html><Text>{self.Arm.mode}</Text></Html>")
    registry.register_blok("mine", "<Html><Text>{self.Arm.mode}</Text></Html>", demo={"deps": {"self": {"Arm": {"mode": "idle"}}}})

    assert built(registry).demo.deps == {"self": {"Arm": {"mode": "", "joints": []}}}  # type: ignore[union-attr]
    assert built(registry, "mine").demo.deps == {"self": {"Arm": {"mode": "idle"}}}  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("source", "code"),
    [
        ("<Html><Button onClick={self.fly()} /></Html>", "deps.unknown_action"),
        ("<Html><Text text={self.tasks.fly.status} /></Html>", "deps.unknown_action"),
        ("<Html><Text text={self.Legs.count} /></Html>", "deps.unknown_state"),
        ("<Html><Text text={self.Arm.speed} /></Html>", "path.unknown"),
        ("<Html><Text text={self.assets.scope} /></Html>", "deps.asset"),
    ],
)
def test_what_the_app_does_not_declare_is_refused(registry: AppRegistry, source: str, code: str) -> None:
    registry.register_blok("panel", source)
    with pytest.raises(BlokError) as caught:
        built(registry)
    assert caught.value.code == code


def test_a_records_returns_are_typed_by_the_action(registry: AppRegistry) -> None:
    with pytest.warns(CatalogWarning, match=r"returns.unnamed"):
        registry.register_blok(
            "panel",
            "<Html><Card><Button onClick={self.move_to(position: 1) into move} /><Text text={move.returns.return0} /></Card></Html>",
        )
    built(registry)

    registry.register_blok("bad", "<Html><Card><Button onClick={self.move_to(position: 1) into move} /><Text text={move.returns.nope} /></Card></Html>")
    with pytest.raises(BlokError, match="'nope' does not exist"):
        built(registry, "bad")


def test_an_agent_blok_reads_no_other_agent(registry: AppRegistry) -> None:
    with pytest.raises(BlokError, match="roots.not_in_kind"):
        registry.register_blok("panel", "<Html><Text text={deps.robot.mode} /></Html>")


def test_locals_are_declared_and_bound(registry: AppRegistry) -> None:
    registry.register_blok(
        "panel",
        "<Html><Card><Slider bind={iterations} /><Button onClick={self.move_to(position: iterations)} /></Card></Html>",
        locals={"iterations": Local(15, persist=True)},
    )

    (local,) = built(registry).locals
    assert (local.key, local.kind, local.default, local.persist) == ("iterations", "INT", 15, True)


def test_a_local_is_a_port_its_kind_inferred_like_an_arguments(registry: AppRegistry) -> None:
    registry.register_blok(
        "panel",
        "<Html><Card><Input bind={name} /><Slider bind={speed} /><Switch bind={on} /><Text text={tags} /><Text text={picked} /></Card></Html>",
        locals={
            "name": "robot",
            "speed": Local[float](),
            "on": Local(True, description="Whether it runs"),
            "tags": Local(["a", "b"]),
            "picked": Local[int | None](),
        },
    )

    ports = {local.key: local for local in built(registry).locals}
    assert {key: port.kind for key, port in ports.items()} == {"name": "STRING", "speed": "FLOAT", "on": "BOOL", "tags": "LIST", "picked": "INT"}
    assert ports["tags"].children is not None and ports["tags"].children[0].kind == "STRING"
    assert ports["picked"].nullable and not ports["speed"].nullable
    assert ports["on"].description == "Whether it runs" and ports["name"].default == "robot"


def test_a_structure_local_is_typed_by_its_identifier() -> None:
    registry = AppRegistry()

    class Image:
        id: str

    async def expand(value: str) -> Image:
        return Image()

    registry.structure_registry.register_as_structure(Image, "@mikro/image", aexpand=expand)

    @registry.register
    def segment(image: Image) -> int:
        """Segment.

        Args:
            image: The image.

        Returns:
            The count.
        """
        return 0

    registry.register_blok(
        "panel",
        "<Html><Button onClick={self.segment(image: picked)} /></Html>",
        locals={"picked": Local[Image | None]()},
    )
    (local,) = built(registry).locals
    assert (local.kind, local.identifier, local.nullable) == ("STRUCTURE", "@mikro/image", True)


@pytest.mark.parametrize("value", [None, [], {}, [1, "a"]])
def test_a_local_nothing_types_is_any(registry: AppRegistry, value: object) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        registry.register_blok("panel", "<Html><Text /></Html>", locals={"x": Local(value)})
        (local,) = built(registry).locals
    assert (local.kind, local.default) == ("ANY", value if value is not None else None)


def test_local_t_keeps_its_type() -> None:
    from typing import Annotated

    assert Local[int]().type is int and Local[int] is Local[int]
    assert Local[int](3).default == 3 and Local(type=str).type is str
    assert Local[Annotated[int, {"unhashable": True}]]().type is not None


def test_a_kind_name_is_no_type(registry: AppRegistry) -> None:
    with pytest.raises(BlokError) as caught:
        registry.register_blok("panel", "<Html><Text /></Html>", locals={"n": Local(15, type="INT")})
    assert caught.value.code == "local.kind" and "not an annotation" in str(caught.value)


def test_a_control_writes_only_a_local_of_its_kind(registry: AppRegistry) -> None:
    with pytest.raises(BlokError) as caught:
        registry.register_blok("panel", "<Html><Switch bind={iterations} /></Html>", locals={"iterations": 15})
    assert caught.value.code == "local.bind_kind"
    registry.register_blok("panel", "<Html><Slider bind={iterations} /></Html>", locals={"iterations": 15})


def test_a_task_call_takes_a_local_of_its_ports_kind(registry: AppRegistry) -> None:
    """``move_to(position: int)``: a string local is refused once the app's actions type ``self``."""
    registry.register_blok("panel", "<Html><Button onClick={self.move_to(position: name)} /></Html>", locals={"name": "a"})

    with pytest.raises(BlokError) as caught:
        built(registry)
    assert caught.value.code == "local.arg_kind"


@pytest.mark.parametrize(
    ("source", "locals_", "code"),
    [
        ("<Html><Slider bind={nope} /></Html>", {}, "local.unknown"),
        ("<Html><Slider bind={n} /></Html>", {"n": Local("x", type=int)}, "local.default"),
    ],
)
def test_a_wrong_local_is_refused(registry: AppRegistry, source: str, locals_: dict, code: str) -> None:
    with pytest.raises(BlokError) as caught:
        registry.register_blok("panel", source, locals=locals_)
    assert caught.value.code == code


def test_a_local_never_takes_a_reserved_root(registry: AppRegistry) -> None:
    with pytest.raises(ValueError, match=r"'self' is reserved.*\[roots.reserved\]"):
        registry.register_blok("panel", "<Html><Text /></Html>", locals={"self": 1})


def test_args_are_read_only_where_declared(registry: AppRegistry) -> None:
    registry.register_blok("panel", "<Html><Text text={args.image} /></Html>", args={"image": int})
    assert [port.key for port in built(registry).args or ()] == ["image"]

    with pytest.raises(BlokError, match="args.unknown"):
        registry.register_blok("other", "<Html><Text text={args.dataset} /></Html>", args={"image": int})
    with pytest.raises(ValueError, match="only an html blok"):
        registry.register_blok("scene", "<Scene />", args={"image": int})


def test_then_is_the_moment_and_now_is_gone(registry: AppRegistry) -> None:
    registry.register_blok("panel", "<Html><Card><Time value={then} /><Badge when={then}>past</Badge></Card></Html>")
    with pytest.raises(BlokError, match="roots.unknown"):
        registry.register_blok("other", "<Html><Text text={now} /></Html>")
    with pytest.raises(TypeError):
        registry.register_blok("other", "<Html><Card /></Html>", now_interval=30)  # type: ignore[call-arg]


def test_an_action_logo_reads_only_its_task(registry: AppRegistry) -> None:
    registry.register_blok("logo", '<ActionLogo><Circle r="2" fill={task.running ? "brand" : "muted"} /></ActionLogo>')
    assert built(registry, "logo").dependencies == ()

    with pytest.raises(BlokError, match="roots.not_in_kind"):
        registry.register_blok("bad", "<ActionLogo><Circle fill={self.Arm.mode} /></ActionLogo>")


def test_a_logo_paints_with_the_pens(registry: AppRegistry) -> None:
    with pytest.raises(BlokError, match="palette.logo_paint"):
        registry.register_blok("logo", '<AgentLogo><Circle fill="#ff0000" /></AgentLogo>')


def test_the_catalogs_are_those_its_components_come_from(registry: AppRegistry) -> None:
    registry.register_blok("panel", "<Html><Card><StructurePicker identifier=\"@mikro/image\" /><div /></Card></Html>")

    assert built(registry).catalogs == ("html@1", "structure@1", "ui@1")


def test_a_blok_written_in_bsx_is_sent_as_its_source(registry: AppRegistry) -> None:
    from arkitekt_spec.blok.bsx import parse_bsx

    registry.register_blok("source", "<Html><Card /></Html>")
    registry.register_blok("tree", parse_bsx("<Html><Card /></Html>"))

    assert (built(registry, "source").source, built(registry, "source").components) == ("<Html><Card /></Html>", None)
    assert built(registry, "tree").components == (parse_bsx("<Html><Card /></Html>"),)


def test_the_wire_carries_vocab_and_only_what_is_declared(registry: AppRegistry) -> None:
    registry.register_blok("panel", "<Html><Card /></Html>")

    wire = built(registry).model_dump(mode="json", by_alias=True, exclude_unset=True)
    assert wire["vocab"] == 2
    assert not {"locals", "queries", "subscriptions", "demo", "nowInterval", "demo_state", "persist", "services", "module", "modules"} & set(wire)


def test_what_the_catalog_does_not_know_is_a_warning_unless_strict(registry: AppRegistry) -> None:
    with pytest.warns(CatalogWarning, match="catalog.unknown_component"):
        registry.register_blok("panel", "<Html><Frobnicator /></Html>")
    with pytest.raises(BlokError, match="catalog.unknown_component"):
        registry.register_blok("strict", "<Html><Frobnicator /></Html>", mode="strict")


def test_a_declared_catalog_is_drawn_with(registry: AppRegistry) -> None:
    from arkitekt_spec.declare.catalogs import ComponentSpec

    registry.declare_ui_catalog("mine", components=[ComponentSpec(name="Gauge", text_prop=None)])
    with warnings.catch_warnings():
        warnings.simplefilter("error", CatalogWarning)
        registry.register_blok("panel", "<Html><Card><Gauge /></Card></Html>", module="mine")


def test_a_new_default_replaces_the_previous_one_of_its_context(registry: AppRegistry) -> None:
    registry.register_blok("a", "<Html><Card /></Html>", default=True)
    registry.register_blok("b", "<Html><Card /></Html>", default=True)

    assert [name for name, blok in registry.registered_bloks.items() if blok.default] == ["b"]


def test_an_overlay_is_an_actions(registry: AppRegistry) -> None:
    with pytest.raises(ValueError, match="an overlay is an action's own"):
        registry.register_blok("o", "<Overlay />")


def test_a_state_cannot_take_a_segment_of_an_agent() -> None:
    registry = AppRegistry()
    with pytest.raises(ValueError, match="A state cannot be called 'tasks'"):

        @registry.state(name="tasks")
        class Tasks:
            count: int


def test_a_module_is_named_not_declared_among_the_catalogs(registry: AppRegistry) -> None:
    """Only the versioned standard catalogs are declared; the Orkestrator's module and an app's
    own are what ``module`` names."""
    from arkitekt_spec.declare.catalogs import ComponentSpec

    registry.declare_ui_catalog("mine", components=[ComponentSpec(name="Gauge")])
    registry.register_blok("extras", "<Html><Resizable><ResizablePanel><Card /></ResizablePanel></Resizable></Html>", mode="strict")
    registry.register_blok("mine", "<Html><Card><Gauge /></Card></Html>", module="mine", mode="strict")
    with warnings.catch_warnings():
        warnings.simplefilter("error", CatalogWarning)
        declared = registry.get_declared_bloks()
    assert (declared["extras"].catalogs, declared["extras"].module) == (("ui@1",), None)
    assert (declared["mine"].catalogs, declared["mine"].module) == (("ui@1",), "mine")


def _caught(registry: AppRegistry, declare: Callable[[], None]) -> list[warnings.WarningMessage]:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        declare()
        registry.get_declared_bloks()
    return [warning for warning in caught if issubclass(warning.category, CatalogWarning)]


def test_a_warning_is_told_once_at_the_line_that_declared_the_blok(registry: AppRegistry) -> None:
    line = 0

    def declare() -> None:
        nonlocal line
        frame = inspect.currentframe()
        assert frame is not None
        line = frame.f_lineno + 1
        registry.register_blok("odd", "<Html><Card><Frobnicator /></Card></Html>")

    found = _caught(registry, declare)
    assert ["[catalog.unknown_component]" in str(warning.message) for warning in found] == [True]
    assert (found[0].filename, found[0].lineno) == (__file__, line)


def test_a_warning_only_the_build_finds_points_at_the_declaration_too() -> None:
    from arkitekt_spec.declare.blok.declarations import Query

    registry = AppRegistry()
    line = 0

    def declare() -> None:
        nonlocal line
        frame = inspect.currentframe()
        assert frame is not None
        line = frame.f_lineno + 1
        registry.register_blok("cells", "<Html><p>{queries.cells.status}</p></Html>", queries={"cells": Query(service="mikro", document="query { datasets { id } }")})

    found = _caught(registry, declare)
    assert ["query.schema" in str(warning.message) for warning in found] == [True]
    assert (found[0].filename, found[0].lineno) == (__file__, line)


def test_a_parse_error_names_the_blok() -> None:
    with pytest.raises(BlokError, match=r"^Blok 'broken': line 1, column \d+: .*\[bsx\.unclosed\]$") as refused:
        AppRegistry().register_blok("broken", "<Html><Card>")
    assert (refused.value.code, refused.value.line) == ("bsx.unclosed", 1)
