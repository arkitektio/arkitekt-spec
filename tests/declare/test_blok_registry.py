"""An app's own bloks: what they read of ``self`` becomes their one dependency, and the whole
tree is checked against it when the agent's input is built."""

import inspect
import warnings
from collections.abc import Callable
from dataclasses import dataclass

import pytest

from arkitekt_spec.blok.vocabulary import BlokError
from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.declare.blok.registry import resolved_locals
from arkitekt_spec.declare.catalogs import CatalogWarning


def panel(content: str) -> str:
    """An agent's panel blok drawing ``content``."""
    return f'<Blok kind="agent" context="panel">{content}</Blok>'


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
        panel("<Card><Text>{self.Arm.mode}</Text><Button onClick={self.move_to(position: 1)}>Go</Button><Progress value={self.tasks.home.progress} /></Card>"),
    )

    (dependency,) = built(registry).dependencies
    assert dependency.key == "self"
    assert [demand.key for demand in dependency.action_dependencies or ()] == ["home", "move_to"]
    assert [demand.key for demand in dependency.state_dependencies or ()] == ["Arm"]


def test_a_blok_reading_nothing_of_self_has_no_dependency(registry: AppRegistry) -> None:
    registry.register_blok("panel", panel("<Card><Text>Hello</Text></Card>"))

    assert built(registry).dependencies == ()


def test_its_demo_is_a_placeholder_per_state_it_reads(registry: AppRegistry) -> None:
    registry.register_blok("panel", panel("<Text>{self.Arm.mode}</Text>"))
    registry.register_blok("mine", panel("<Text>{self.Arm.mode}</Text>"), demo={"deps": {"self": {"Arm": {"mode": "idle"}}}})

    assert built(registry).demo.deps == {"self": {"Arm": {"mode": "", "joints": []}}}  # type: ignore[union-attr]
    assert built(registry, "mine").demo.deps == {"self": {"Arm": {"mode": "idle"}}}  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("source", "code"),
    [
        (panel("<Button onClick={self.fly()} />"), "deps.unknown_action"),
        (panel("<Text text={self.tasks.fly.status} />"), "deps.unknown_action"),
        (panel("<Text text={self.Legs.count} />"), "deps.unknown_state"),
        (panel("<Text text={self.Arm.speed} />"), "path.unknown"),
        (panel("<Text text={self.assets.scope} />"), "deps.asset"),
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
            panel("<Card><Button onClick={self.move_to(position: 1) into move} /><Text text={move.returns.return0} /></Card>"),
        )
    built(registry)

    registry.register_blok("bad", panel("<Card><Button onClick={self.move_to(position: 1) into move} /><Text text={move.returns.nope} /></Card>"))
    with pytest.raises(BlokError, match="'nope' does not exist"):
        built(registry, "bad")


def test_an_agent_blok_reads_no_other_agent(registry: AppRegistry) -> None:
    with pytest.raises(BlokError, match="roots.not_in_kind"):
        registry.register_blok("panel", panel("<Text text={deps.robot.mode} />"))


def test_locals_are_declared_and_bound(registry: AppRegistry) -> None:
    registry.register_blok(
        "panel",
        panel('<local name="iterations" default={15} persist /><Card><Slider bind={iterations} /><Button onClick={self.move_to(position: iterations)} /></Card>'),
    )

    (local,) = resolved_locals(built(registry), registry).values()
    assert (local.key, local.kind, local.default, local.persist) == ("iterations", "INT", 15, True)


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

    registry.register_blok("panel", panel('<local name="picked" nullable /><Button onClick={self.segment(image: picked)} />'))
    (local,) = resolved_locals(built(registry), registry).values()
    assert (local.kind, local.identifier, local.nullable) == ("STRUCTURE", "@mikro/image", True)


def test_a_task_call_takes_a_local_of_its_ports_kind(registry: AppRegistry) -> None:
    """``move_to(position: int)``: a string local is refused once the app's actions type ``self``."""
    registry.register_blok("panel", panel('<local name="name" default={"a"} /><Button onClick={self.move_to(position: name)} />'))
    with pytest.raises(BlokError) as caught:
        built(registry)
    assert caught.value.code == "local.arg_kind"


def test_args_are_read_only_where_declared(registry: AppRegistry) -> None:
    registry.register_blok("panel", panel("<Text text={args.image} />"), args={"image": int})
    assert [port.key for port in built(registry).args or ()] == ["image"]

    with pytest.raises(BlokError, match="args.unknown"):
        registry.register_blok("other", panel("<Text text={args.dataset} />"), args={"image": int})
    with pytest.raises(ValueError, match="only a panel blok"):
        registry.register_blok("scene", '<Blok kind="agent" context="scene"><Scene /></Blok>', args={"image": int})


def test_then_is_the_moment_and_now_is_gone(registry: AppRegistry) -> None:
    registry.register_blok("panel", panel("<Card><Time value={then} /><Badge when={then}>past</Badge></Card>"))
    with pytest.raises(BlokError, match="roots.unknown"):
        registry.register_blok("other", panel("<Text text={now} />"))
    with pytest.raises(TypeError):
        registry.register_blok("other", panel("<Card />"), now_interval=30)  # type: ignore[call-arg]


def test_an_action_logo_reads_only_its_task(registry: AppRegistry) -> None:
    registry.register_blok("logo", '<Blok kind="action" context="logo"><Logo><Circle r="2" fill={task.running ? "brand" : "muted"} /></Logo></Blok>')
    assert built(registry, "logo").dependencies == ()

    with pytest.raises(BlokError, match="roots.not_in_kind"):
        registry.register_blok("bad", '<Blok kind="action" context="logo"><Logo><Circle fill={self.Arm.mode} /></Logo></Blok>')


def test_a_logo_paints_with_the_pens(registry: AppRegistry) -> None:
    with pytest.raises(BlokError, match="palette.logo_paint"):
        registry.register_blok("logo", '<Blok kind="agent" context="logo"><Logo><Circle fill="#ff0000" /></Logo></Blok>')


def test_a_blok_of_the_base_catalog_names_none(registry: AppRegistry) -> None:
    registry.register_blok("panel", panel("<Card><StructurePicker identifier=\"@mikro/image\" /><div /></Card>"))

    blok = built(registry)
    assert (blok.catalog, blok.catalog_minor) == (None, None)
    assert not {"catalog", "catalog_minor"} & blok.model_fields_set, "nothing to name, nothing sent"


def test_a_blok_written_in_bsx_is_sent_as_its_source(registry: AppRegistry) -> None:
    from arkitekt_spec.blok.bsx import parse_bsx

    registry.register_blok("source", panel("<Card />"))
    registry.register_blok("tree", parse_bsx(panel("<Card />")))

    assert (built(registry, "source").source, built(registry, "source").components) == (panel("<Card />"), None)
    assert built(registry, "tree").components == (parse_bsx(panel("<Card />")),)


def test_a_blok_declared_as_its_source_is_registered_without_its_dependency(registry: AppRegistry) -> None:
    """The server works the ``self`` dependency out from the source; what is built here keeps it."""
    registry.register_blok("panel", panel("<Card><Text>{self.Arm.mode}</Text></Card>"))

    (sent,) = registry.to_registered_agent_input().model_dump(mode="json", by_alias=False, exclude_unset=True)["bloks"]
    assert "dependencies" not in sent
    assert (sent["source"], sent["context"], sent["vocab"]) == (panel("<Card><Text>{self.Arm.mode}</Text></Card>"), "PANEL", 2)
    # Checked against, served and inspected with its dependency, as before.
    assert [dependency.key for dependency in built(registry).dependencies] == ["self"]
    assert [dependency.key for dependency in registry.to_implement_agent_input().bloks[0].dependencies] == ["self"]


def test_a_blok_declared_as_its_tree_is_registered_as_the_source_that_says_it(registry: AppRegistry) -> None:
    """No tree is put on the wire that BSX can say; one it cannot say is sent as it was declared."""
    from arkitekt_spec.blok.bsx import parse_bsx

    tree = parse_bsx(panel("<Card><Text>{self.Arm.mode}</Text></Card>"))
    registry.register_blok("printed", tree)
    registry.register_blok("hand_built", tree.model_copy(update={"id": "root"}))

    sent = {blok["key"]: blok for blok in registry.to_registered_agent_input().model_dump(mode="json", by_alias=False, exclude_unset=True)["bloks"]}
    assert "components" not in sent["printed"] and "dependencies" not in sent["printed"]
    assert parse_bsx(sent["printed"]["source"]) == tree
    assert "source" not in sent["hand_built"]
    assert [dependency["key"] for dependency in sent["hand_built"]["dependencies"]] == ["self"]


def test_the_wire_carries_vocab_and_only_what_is_declared(registry: AppRegistry) -> None:
    registry.register_blok("panel", panel("<Card />"))

    wire = built(registry).model_dump(mode="json", by_alias=True, exclude_unset=True)
    assert wire["vocab"] == 2
    assert not {"locals", "queries", "subscriptions", "demo", "nowInterval", "demo_state", "persist", "services", "module", "modules"} & set(wire)


def test_what_the_catalog_does_not_know_is_a_warning_unless_strict(registry: AppRegistry) -> None:
    with pytest.warns(CatalogWarning, match="catalog.unknown_component"):
        registry.register_blok("panel", panel("<Frobnicator />"))
    with pytest.raises(BlokError, match="catalog.unknown_component"):
        registry.register_blok("strict", panel("<Frobnicator />"), mode="strict")


def test_a_declared_catalog_is_drawn_with(registry: AppRegistry) -> None:
    from arkitekt_spec.declare.catalogs import ComponentSpec

    registry.declare_ui_catalog("mine", components=[ComponentSpec(name="Gauge", text_prop=None)])
    with warnings.catch_warnings():
        warnings.simplefilter("error", CatalogWarning)
        registry.register_blok("panel", '<Blok kind="agent" context="panel" catalog="mine"><Card><Gauge /></Card></Blok>')


def test_a_new_default_replaces_the_previous_one_of_its_context(registry: AppRegistry) -> None:
    registry.register_blok("a", panel("<Card />"), default=True)
    registry.register_blok("b", panel("<Card />"), default=True)

    assert [name for name, blok in registry.registered_bloks.items() if blok.default] == ["b"]


def test_a_state_cannot_take_a_segment_of_an_agent() -> None:
    registry = AppRegistry()
    with pytest.raises(ValueError, match="A state cannot be called 'tasks'"):

        @registry.state(name="tasks")
        class Tasks:
            count: int


def test_a_blok_names_the_one_catalog_it_is_written_for(registry: AppRegistry) -> None:
    """The base catalog draws ``Resizable`` itself; what an app catalog adds is named: the app's
    own by its name, a published one by its address."""
    from arkitekt_spec.blok.catalogs import ORKESTRATOR_CATALOG_ID
    from arkitekt_spec.declare.catalogs import ComponentSpec

    registry.declare_ui_catalog("mine", components=[ComponentSpec(name="Gauge")])
    registry.register_blok("extras", panel("<Resizable><ResizablePanel><Card /></ResizablePanel></Resizable>"), mode="strict")
    registry.register_blok("mine", '<Blok kind="agent" context="panel" catalog="mine"><Card><Gauge /></Card></Blok>', mode="strict")
    scene = "<MikroScene value={args.scene}><MikroSceneViewport /></MikroScene></Blok>"
    registry.register_blok("scene", f'<Blok kind="agent" context="panel" catalog="{ORKESTRATOR_CATALOG_ID}">' + scene, args={"scene": str}, mode="strict")
    with warnings.catch_warnings():
        warnings.simplefilter("error", CatalogWarning)
        declared = registry.get_declared_bloks()
    assert {key: blok.catalog for key, blok in declared.items()} == {"extras": None, "mine": "mine", "scene": ORKESTRATOR_CATALOG_ID}


def test_a_component_of_a_catalog_the_blok_is_not_written_for_is_refused(registry: AppRegistry) -> None:
    with pytest.raises(BlokError, match=r"<MikroSceneProbe> is of https://arkitekt.live/catalogs/orkestrator/v1, and this blok is written for https://arkitekt.live/catalogs/base/v4") as refused:
        registry.register_blok("panel", panel("<MikroSceneProbe />"))
    assert refused.value.code == "catalog.target"


def test_what_names_no_catalog_at_all_is_refused(registry: AppRegistry) -> None:
    with pytest.raises(ValueError, match="manifest.catalog"):
        registry.register_blok("panel", '<Blok kind="agent" context="panel" catalog="ui@1"><Card /></Blok>')


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
        registry.register_blok("odd", panel("<Card><Frobnicator /></Card>"))

    found = _caught(registry, declare)
    assert ["[catalog.unknown_component]" in str(warning.message) for warning in found] == [True]
    assert (found[0].filename, found[0].lineno) == (__file__, line)


def test_a_warning_only_the_build_finds_points_at_the_declaration_too() -> None:
    registry = AppRegistry()
    line = 0

    def declare() -> None:
        nonlocal line
        frame = inspect.currentframe()
        assert frame is not None
        line = frame.f_lineno + 1
        registry.register_blok("cells", panel('<query name="cells" service="mikro" document="query { datasets { id } }" /><p>{queries.cells.status}</p>'))

    found = _caught(registry, declare)
    assert ["query.schema" in str(warning.message) for warning in found] == [True]
    assert (found[0].filename, found[0].lineno) == (__file__, line)


def test_a_parse_error_names_the_blok() -> None:
    with pytest.raises(BlokError, match=r"^Blok 'broken': line 1, column \d+: .*\[bsx\.unclosed\]$") as refused:
        AppRegistry().register_blok("broken", '<Blok kind="agent" context="panel"><Card>')
    assert (refused.value.code, refused.value.line) == ("bsx.unclosed", 1)


def test_a_catalog_warning_carries_its_finding_and_blok(registry: AppRegistry) -> None:
    """What ``arkitekt check --json-out`` places in the source: no parsing of the message."""
    with pytest.warns(CatalogWarning) as caught:
        registry.register_blok("panel", panel("<Frobnicator />"))

    (warning,) = [record.message for record in caught if isinstance(record.message, CatalogWarning)]
    assert warning.blok == "panel"
    assert warning.finding is not None and warning.finding.code == "catalog.unknown_component"
    assert warning.finding.path is not None and "Frobnicator" in warning.finding.path
    assert "[catalog.unknown_component]" in str(warning), "the told message is unchanged"


def test_a_refused_blok_names_the_node_it_refuses(registry: AppRegistry) -> None:
    with pytest.raises(BlokError) as raised:
        registry.register_blok("strict", panel("<Frobnicator />"), mode="strict")

    assert raised.value.code == "catalog.unknown_component"
    assert raised.value.node is not None and "Frobnicator" in raised.value.node
    assert raised.value.within("Blok 'strict'").node == raised.value.node
