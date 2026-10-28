"""An action's own bloks: declared inline with the action, one per context (named by the root:
``<Html>``, ``<Scene>``, ``<Overlay>``), checked at decoration.

They read the run as ``task``, its args as ``args``, the agents the run got as ``deps.<key>`` (the
action's dependency parameters), and are not part of the action's identity: an action that gains
or loses a blok is the same action, with the same hash.
"""

from typing import Protocol

import pytest
from pydantic import ValidationError

from arkitekt_spec.actions import ActionBlokInput, DefinitionInput, definition_hash
from arkitekt_spec.blok.bsx import parse_bsx
from arkitekt_spec.blok.catalogs import default_view
from arkitekt_spec.blok.vocabulary import BlokError
from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.declare.blok.action import ActionBlok, ActionBloks
from arkitekt_spec.declare.blok.declarations import Local
from arkitekt_spec.declare.blok.parser import bsx
from arkitekt_spec.declare.blok.infer import resolve_scope
from arkitekt_spec.declare.blok.validate import BlokScope
from arkitekt_spec.declare.definition.errors import DefinitionError


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


OVERLAY = """
<Overlay>
  <Anchor dep="robot">
    <Glow on={task.running} />
    <Popout open={task.ask}><ask /><Switch bind={show_path} label="Path" /></Popout>
  </Anchor>
  <if when={show_path}>
    <Route through={["robot", "frame", "robot"]} progress={task.progress} />
  </if>
</Overlay>
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
        ActionBlok(OVERLAY, locals={"show_path": Local(True, persist=True)}),
        ActionBlok("<Html><Card><Progress value={task.progress} /><ask /></Card></Html>", description="The run"),
    )
    overlay, panel = definition.bloks
    assert (overlay.context, overlay.source, overlay.components, overlay.vocab) == ("OVERLAY", OVERLAY, None, 2)
    assert [(local.key, local.kind, local.default, local.persist) for local in overlay.locals] == [("show_path", "BOOL", True, True)]
    assert (overlay.catalogs, overlay.module) == (("scene@1", "ui@1"), None)
    assert (panel.context, panel.description) == ("HTML", "The run")

    wire = definition.model_dump(mode="json", by_alias=False, exclude_unset=True)
    assert [blok["context"] for blok in wire["bloks"]] == ["OVERLAY", "HTML"]
    assert "locals" not in wire["bloks"][1], "nothing declared, nothing sent"


def test_bloks_are_not_identity() -> None:
    with_bloks = registered("<Html><Card><ask /></Card></Html>")
    without = registered()

    assert definition_hash(with_bloks) == definition_hash(without)
    assert "bloks" not in without.model_dump(exclude_unset=True)


def test_an_anchor_names_a_dependency_of_the_action() -> None:
    with pytest.raises(ValueError, match="names 'stage', which is not a dependency"):
        registered('<Overlay><Anchor dep="stage" /></Overlay>')


def test_a_blok_reads_only_what_the_action_has() -> None:
    with pytest.raises(BlokError, match=r"'nowhere' is not a dependency of the blok .*\[deps.unknown\]"):
        registered("<Html><Card><Text text={deps.nowhere.x} /></Card></Html>")
    with pytest.raises(BlokError, match=r"reads args.image, which the blok does not declare"):
        registered("<Html><Card><Text text={args.image} /></Card></Html>")
    registered("<Html><Card><Text text={args.slots} /><Button onClick={deps.robot.pick(slot: 1)} /></Card></Html>")


@pytest.mark.parametrize(
    ("tree", "context"),
    [
        ("<Html><Card><ask /></Card></Html>", "HTML"),
        ("<Scene />", "SCENE"),
        ('<Overlay><Anchor dep="robot" /></Overlay>', "OVERLAY"),
    ],
)
def test_a_bloks_root_names_its_context(tree: str, context: str) -> None:
    (blok,) = registered(tree).bloks or ()
    assert blok.context == context


def test_a_root_that_names_no_context_is_refused() -> None:
    with pytest.raises(BlokError, match="context.root_unknown") as raised:
        registered("<Card><ask /></Card>")
    assert raised.value.code == "context.root_unknown"


def test_one_blok_needs_no_list() -> None:
    """A bare BSX string is one blok (not iterated char by char), as is a lone ActionBlok."""
    (from_string,) = registered_with("<Html><Card><ask /></Card></Html>").bloks or ()
    assert from_string.context == "HTML"
    (from_blok,) = registered_with(ActionBlok("<Html><Card><ask /></Card></Html>", description="The run")).bloks or ()
    assert (from_blok.context, from_blok.description) == ("HTML", "The run")


def test_one_blok_per_context_in_the_contexts_an_action_has() -> None:
    with pytest.raises(ValueError, match="given twice"):
        registered("<Html><Card /></Html>", "<Html><Card /></Html>")
    with pytest.raises(ValueError, match="<Html>, <Scene> or <Overlay>"):
        registered("<AgentLogo />")


def test_an_overlay_is_an_actions_blok_only() -> None:
    registry = AppRegistry()
    with pytest.raises(ValueError, match="an overlay is an action's own"):
        registry.register_blok("over", "<Overlay />")


def test_an_inline_action_logo_is_rooted_in_the_action_logo() -> None:
    registry = AppRegistry()

    def crop(slot: int) -> int:
        """Crop."""
        return slot

    with pytest.raises(DefinitionError, match="an inline logo is an <ActionLogo>, not <AgentLogo>"):
        registry.register_action(crop, key="crop", logo=bsx('<AgentLogo><Path d="M0 0" fill="brand" /></AgentLogo>'))


def test_a_local_is_no_root_the_renderer_fills() -> None:
    with pytest.raises(BlokError, match="roots.reserved"):
        registered(ActionBlok("<Html><Card /></Html>", locals={"task": 1}))


def test_only_an_actions_blok_reads_a_run() -> None:
    registry = AppRegistry()
    with pytest.raises(ValueError, match="'ask' .* reads a run of an action"):
        registry.register_blok("panel", "<Html><Card><ask /></Card></Html>")


def test_the_wire_model_checks_as_the_server_does() -> None:
    with pytest.raises(ValidationError, match=r"components or as its BSX source.*\[manifest.sent\]"):
        ActionBlokInput(vocab=2, context="HTML")
    with pytest.raises(ValidationError, match=r"HTML, SCENE, OVERLAY, not LOGO.*\[context.kind\]"):
        ActionBlokInput(vocab=2, context="LOGO", source="<AgentLogo />")
    with pytest.raises(ValidationError, match="vocab"):
        ActionBlokInput.model_validate({"context": "HTML", "source": "<Html><Card /></Html>"})
    with pytest.raises(ValidationError, match="mutation"):
        ActionBlokInput.model_validate(
            {"vocab": 2, "source": "<Html><Card /></Html>", "queries": [{"key": "q", "service": "mikro", "document": "mutation { x }"}]}
        )


def test_a_component_of_shadcn_declares_shadcn() -> None:
    import warnings

    from arkitekt_spec.declare.catalogs import CatalogWarning

    with warnings.catch_warnings():
        warnings.simplefilter("error", CatalogWarning)
        (blok,) = registered("<Html><Sheet><SheetContent><ask /></SheetContent></Sheet></Html>").bloks
    assert (blok.catalogs, blok.module) == (("shadcn@1",), None)


def test_an_action_blok_names_its_module_and_catalogs() -> None:
    (blok,) = registered(ActionBlok("<Html><Card><ask /></Card></Html>", catalogs=["ui@1"], module="orkestrator")).bloks

    assert (blok.catalogs, blok.module) == (("ui@1",), "orkestrator")


def test_an_action_blok_local_is_typed_by_local_t() -> None:
    (blok,) = registered(ActionBlok("<Html><Card><Slider bind={n} /><ask /></Card></Html>", locals={"n": Local[float]()})).bloks

    (local,) = blok.locals
    assert (local.key, local.kind, local.default) == ("n", "FLOAT", None)


def test_an_action_blok_local_nothing_types_is_any() -> None:
    definition = registered(ActionBlok("<Html><Card><ask /></Card></Html>", locals={"x": None}))
    (blok,) = definition.bloks or ()
    (local,) = blok.locals
    assert (local.key, local.kind) == ("x", "ANY")


def test_an_action_blok_local_takes_the_kind_of_the_control_bound_to_it() -> None:
    """It is sent as written; the tree resolves it as a server stores it."""
    definition = registered(ActionBlok("<Html><Card><Slider bind={x} /></Card></Html>", locals={"x": None}))
    (blok,) = definition.bloks or ()
    (local,) = blok.locals
    assert "kind" not in local.model_fields_set
    assert blok.source is not None
    (resolved,) = resolve_scope(parse_bsx(blok.source), BlokScope(kind="action", locals=tuple(blok.locals)), default_view()).locals
    assert resolved.kind == "FLOAT"


def test_a_refused_action_blok_keeps_its_code_node_and_hint() -> None:
    with pytest.raises(BlokError) as raised:
        registered("<Html><Card><Text text={deps.nowhere.x} /></Card></Html>")

    assert raised.value.code == "deps.unknown"
    assert raised.value.node is not None and "Text" in raised.value.node
    assert "Action 'process' (html blok" in str(raised.value)


def test_an_action_blok_warning_names_the_action_and_context() -> None:
    from arkitekt_spec.declare.catalogs import CatalogWarning

    with pytest.warns(CatalogWarning) as caught:
        registered("<Html><Card><Frobnicator /><ask /></Card></Html>")

    warnings = [record.message for record in caught if isinstance(record.message, CatalogWarning)]
    assert {warning.blok for warning in warnings} == {"process:html"}
    assert {warning.finding.code for warning in warnings if warning.finding is not None} == {"catalog.unknown_component"}
