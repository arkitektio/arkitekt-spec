"""An action's own bloks: declared inline with the action, one per kind and context (a
``<Blok kind="action|assign" context="panel|scene">``), checked at decoration.

They read the run as ``task``, its args as ``args``, the agents the run got as ``deps.<key>`` (the
action's dependency parameters), and are not part of the action's identity: an action that gains
or loses a blok is the same action, with the same hash.
"""

from typing import Protocol

import pytest
from pydantic import ValidationError

from arkitekt_spec.actions import ActionBlokInput, DefinitionInput, definition_hash
from arkitekt_spec.blok.bsx import parse_bsx
from arkitekt_spec.blok.vocabulary import BlokError
from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.declare.blok.action import ActionBlok, ActionBloks
from arkitekt_spec.declare.blok.infer import resolve_locals
from arkitekt_spec.declare.blok.parser import bsx
from arkitekt_spec.declare.definition.errors import DefinitionError

PANEL = '<Blok kind="action" context="panel">'
SCENE = '<Blok kind="action" context="scene">'
ASSIGN = '<Blok kind="assign" context="panel">'


class Arm(Protocol):
    """An arm another app runs."""

    def pick(self, slot: int) -> None:
        """Pick."""
        ...


class Scope(Protocol):
    """A microscope another app runs."""

    def image(self, slot: int) -> float:
        """Image."""
        ...


SPACE = """<Blok kind="action" context="scene">
<local name="show_path" default={true} persist />
<Space room="room:lab">
  <Placed dep="robot" matrix={[[1, 0, 0, 0.8], [0, 1, 0, 0], [0, 0, 1, 0.15], [0, 0, 0, 1]]}>
    <Glow on={task.running} />
    <Popout open={task.ask}><ask /><Switch bind={show_path} label="Path" /></Popout>
  </Placed>
  <Placed dep="frame" />
  <if when={show_path}>
    <Route through={["robot", "frame", "robot"]} progress={task.progress} />
  </if>
</Space>
</Blok>
"""


def registered(*bloks: ActionBlok | str) -> DefinitionInput:
    return registered_with(list(bloks) or None)


def registered_with(bloks: ActionBloks | None) -> DefinitionInput:
    """``process`` with ``bloks=`` passed through as given (one blok, a list, or none)."""
    registry = AppRegistry()
    registry.declare(app="arm")(Arm)
    registry.declare(app="scope")(Scope)

    def process(robot: Arm, frame: Scope, slots: list[int]) -> float:
        """Process slides."""
        robot.pick(slots[0])
        return frame.image(slots[0])

    registry.register_workflow(process, bloks=bloks)
    return registry.implementations["process"].definition


def test_bloks_reach_the_definition_as_written() -> None:
    definition = registered(
        SPACE,
        ActionBlok(PANEL + "<Card><Progress value={task.progress} /><ask /></Card></Blok>", description="The run"),
    )
    space, panel = definition.bloks
    assert (space.context, space.source, space.components, space.vocab) == ("SCENE", SPACE, None, 2)
    assert space.source is not None
    (show_path,) = resolve_locals(parse_bsx(space.source)).values()
    assert (show_path.local.key, show_path.local.kind, show_path.local.default, show_path.local.persist) == ("show_path", "BOOL", True, True)
    assert (space.catalog, space.catalog_minor) == (None, None), "written for the base catalog: nothing to name"
    assert (panel.context, panel.description) == ("PANEL", "The run")

    wire = definition.model_dump(mode="json", by_alias=False, exclude_unset=True)
    assert [blok["context"] for blok in wire["bloks"]] == ["SCENE", "PANEL"]


def test_bloks_are_not_identity() -> None:
    with_bloks = registered(PANEL + "<Card><ask /></Card></Blok>")
    without = registered()

    assert definition_hash(with_bloks) == definition_hash(without)
    assert "bloks" not in without.model_dump(exclude_unset=True)


def test_a_placed_names_a_dependency_of_the_action() -> None:
    with pytest.raises(BlokError, match=r"Placed.dep: 'stage' is not a dependency of the blok .*\[deps.unknown\]") as raised:
        registered(SCENE + '<Space room="lab"><Placed dep="stage" /></Space></Blok>')
    assert raised.value.node is not None and "Placed" in raised.value.node
    with pytest.raises(BlokError, match=r"Route.through: 'stage' is not a dependency"):
        registered(SCENE + '<Space room="lab"><Route through={["robot", "stage"]} /></Space></Blok>')


def test_a_blok_reads_only_what_the_action_has() -> None:
    with pytest.raises(BlokError, match=r"'nowhere' is not a dependency of the blok .*\[deps.unknown\]"):
        registered(PANEL + "<Card><Text text={deps.nowhere.x} /></Card></Blok>")
    with pytest.raises(BlokError, match=r"reads args.image, which the blok does not declare"):
        registered(PANEL + "<Card><Text text={args.image} /></Card></Blok>")
    registered(PANEL + "<Card><Text text={args.slots} /><Button onClick={deps.robot.pick(slot: 1)} /></Card></Blok>")


@pytest.mark.parametrize(
    ("tree", "context"),
    [
        (PANEL + "<Card><ask /></Card></Blok>", "PANEL"),
        (SCENE + "<Scene /></Blok>", "SCENE"),
        (SCENE + '<Space room="lab"><Placed dep="robot" /></Space></Blok>', "SCENE"),
    ],
)
def test_a_blok_says_its_context(tree: str, context: str) -> None:
    (blok,) = registered(tree).bloks or ()
    assert blok.context == context


def test_one_blok_needs_no_list() -> None:
    """A bare BSX string is one blok (not iterated char by char), as is a lone ActionBlok."""
    (from_string,) = registered_with(PANEL + "<Card><ask /></Card></Blok>").bloks or ()
    assert from_string.context == "PANEL"
    (from_blok,) = registered_with(ActionBlok(PANEL + "<Card><ask /></Card></Blok>", description="The run")).bloks or ()
    assert (from_blok.context, from_blok.description) == ("PANEL", "The run")


def test_one_blok_per_context_in_the_contexts_an_action_has() -> None:
    with pytest.raises(ValueError, match="given twice"):
        registered(PANEL + "<Card /></Blok>", PANEL + "<Card /></Blok>")
    with pytest.raises(ValueError, match="a panel or a scene, not a logo"):
        registered('<Blok kind="action" context="logo"><Logo /></Blok>')
    with pytest.raises(BlokError, match="kind.placement"):
        registered('<Blok kind="agent" context="panel"><Card /></Blok>')


def test_a_space_is_an_actions_blok_only() -> None:
    """It places the run's dependencies, which only an action's blok has."""
    registry = AppRegistry()
    with pytest.raises(ValueError, match="'Space' .* reads a run of an action"):
        registry.register_blok("lab", '<Blok kind="agent" context="scene"><Space room="lab" /></Blok>')
    with pytest.raises(ValueError, match="'Placed' .* reads a run of an action"):
        registry.register_blok("lab", '<Blok kind="agent" context="scene"><Scene><Placed dep="robot" /></Scene></Blok>')
    with pytest.raises(ValueError, match="goes with its action"):
        registry.register_blok("lab", SCENE + '<Space room="lab" /></Blok>')


def test_an_inline_action_logo_is_an_action_logo_blok() -> None:
    registry = AppRegistry()

    def crop(slot: int) -> int:
        """Crop."""
        return slot

    with pytest.raises(DefinitionError, match='an inline logo is a <Blok kind="action" context="logo">, not an agent blok in logo'):
        registry.register_action(crop, key="crop", logo=bsx('<Blok kind="agent" context="logo"><Logo><Path d="M0 0" fill="brand" /></Logo></Blok>'))


def test_only_an_actions_blok_reads_a_run() -> None:
    registry = AppRegistry()
    with pytest.raises(ValueError, match="'ask' .* reads a run of an action"):
        registry.register_blok("panel", '<Blok kind="agent" context="panel"><Card><ask /></Card></Blok>')


def test_the_wire_model_checks_as_the_server_does() -> None:
    with pytest.raises(ValidationError, match=r"components or as its BSX source.*\[manifest.sent\]"):
        ActionBlokInput(vocab=2, context="PANEL")
    with pytest.raises(ValidationError, match=r"not LOGO.*\[context.kind\]"):
        ActionBlokInput(vocab=2, context="LOGO", source='<Blok kind="action" context="logo"><Logo /></Blok>')
    with pytest.raises(ValidationError, match="vocab"):
        ActionBlokInput.model_validate({"context": "PANEL", "source": PANEL + "<Card /></Blok>"})


def test_every_family_of_the_base_catalog_is_drawn_without_naming_one() -> None:
    import warnings

    from arkitekt_spec.declare.catalogs import CatalogWarning

    with warnings.catch_warnings():
        warnings.simplefilter("error", CatalogWarning)
        (blok,) = registered(PANEL + "<Sheet><SheetContent><ask /></SheetContent></Sheet></Blok>").bloks
    assert (blok.catalog, blok.catalog_minor) == (None, None)
    assert "catalog" not in blok.model_dump(exclude_unset=True)


def test_an_action_blok_names_the_catalog_it_is_written_for() -> None:
    from arkitekt_spec.blok.catalogs import ORKESTRATOR_CATALOG_ID

    content = "<MikroScene value={task.args.scene}><MikroSceneViewport /></MikroScene><ask /></Blok>"
    (blok,) = registered(f'<Blok kind="action" context="panel" catalog="{ORKESTRATOR_CATALOG_ID}">' + content).bloks

    assert (blok.catalog, blok.catalog_minor) == (ORKESTRATOR_CATALOG_ID, None)
    with pytest.raises(BlokError, match=r"<MikroScene> is of https://arkitekt.live/catalogs/orkestrator/v1.*\[catalog.target\]"):
        registered(PANEL + content)


def test_a_refused_action_blok_keeps_its_code_node_and_hint() -> None:
    with pytest.raises(BlokError) as raised:
        registered(PANEL + "<Card><Text text={deps.nowhere.x} /></Card></Blok>")

    assert raised.value.code == "deps.unknown"
    assert raised.value.node is not None and "Text" in raised.value.node
    assert "Action 'process' (panel blok" in str(raised.value)


def test_an_action_blok_warning_names_the_action_and_context() -> None:
    from arkitekt_spec.declare.catalogs import CatalogWarning

    with pytest.warns(CatalogWarning) as caught:
        registered(PANEL + "<Card><Frobnicator /><ask /></Card></Blok>")

    warnings = [record.message for record in caught if isinstance(record.message, CatalogWarning)]
    assert {warning.blok for warning in warnings} == {"process:panel"}
    assert {warning.finding.code for warning in warnings if warning.finding is not None} == {"catalog.unknown_component"}


PREPARE = """
<Blok kind="assign" context="panel">
  <Row>
    <Space room="room:lab" height="24rem"><Placed dep="robot" /></Space>
    <Card>
      <Button disabled={homing.running} onClick={deps.robot.pick(slot: 1) into homing}>Home</Button>
      <Text>{args.slots}</Text>
      <args />
      <Button onClick={assign.start()}>Start</Button>
    </Card>
  </Row>
</Blok>
"""


def registered_to_start(bloks: ActionBloks | None) -> DefinitionInput:
    registry = AppRegistry()
    registry.declare(app="arm")(Arm)

    def process(robot: Arm, slots: list[int]) -> None:
        """Process slides."""
        robot.pick(slots[0])

    registry.register_workflow(process, bloks=bloks)
    return registry.implementations["process"].definition


def test_an_assign_blok_is_the_actions_own_for_starting_a_run() -> None:
    definition = registered_to_start([PANEL + "<Card><ask /></Card></Blok>", PREPARE])

    assert [(blok.phase, blok.context) for blok in definition.bloks] == [("RUN", "PANEL"), ("ASSIGN", "PANEL")]
    wire = definition.model_dump(mode="json", by_alias=False, exclude_unset=True)
    assert [blok.get("phase") for blok in wire["bloks"]] == [None, "ASSIGN"], "a run's blok keeps its bytes"
    assert definition_hash(definition) == definition_hash(registered_to_start(None))


def test_starting_and_watching_keep_to_their_own_bloks() -> None:
    with pytest.raises(BlokError, match=r"an assign blok has no 'task'"):
        registered_to_start(ASSIGN + "<Text>{task.status}</Text></Blok>")
    with pytest.raises(ValueError, match="holds no 'ask'"):
        registered_to_start(ASSIGN + "<ask /></Blok>")
    with pytest.raises(BlokError, match=r"<args> belongs to assign bloks.*\[catalog.kind\]"):
        registered_to_start(PANEL + "<args /></Blok>")
    with pytest.raises(BlokError, match=r"assign.start\(\) belongs to starting a run"):
        registered_to_start(PANEL + "<Button onClick={assign.start()}>Go</Button></Blok>")
    with pytest.raises(ValueError, match="one assign blok per context"):
        registered_to_start([ASSIGN + "<args /></Blok>", ASSIGN + "<args /></Blok>"])
