"""An action's own bloks: declared inline with the action, one per context, checked at decoration.

They name devices only by the action's dependency parameters, read the run under ``@task``, and are
not part of the action's identity: an action that gains or loses a blok is the same action, with
the same hash. An action without bloks registers exactly as it did before actions had any.
"""

import warnings
from typing import Protocol

import pytest
from pydantic import ValidationError

from arkitekt_spec.actions import ActionBlokInput, DefinitionInput, definition_hash
from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.declare.blok.action import ActionBlok, through_keys
from arkitekt_spec.declare.catalogs import CatalogWarning


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
  <At dependency="robot">
    <Popout open="@task.ask"><TaskAsk /></Popout>
  </At>
  <if condition="@show_path">
    <Route through="['robot', 'frame', 'robot']" progress="@task.progress" />
  </if>
</Overlay>
"""


def registered(*bloks: ActionBlok) -> DefinitionInput:
    registry = AppRegistry()
    registry.declare(app="arm")(Arm)
    registry.declare(app="scope")(Scope)

    def process(robot: Arm, frame: Scope, slots: list[int]) -> float:
        """Process slides."""
        robot.pick(slots[0])
        return frame.image(slots[0])

    with warnings.catch_warnings():
        # The standard catalog does not know the overlay's components until the orkestrator ships them.
        warnings.simplefilter("ignore", CatalogWarning)
        registry.register_workflow(process, bloks=list(bloks) or None)
    return registry.implementations["process"].definition


def test_bloks_reach_the_definition_as_written() -> None:
    definition = registered(
        ActionBlok(OVERLAY, context="overlay", local_state={"show_path": True}, persist=["show_path"]),
        ActionBlok('<Card><Progress value="@task.progress" /><TaskAsk /></Card>', context="html", description="The run"),
    )
    overlay, panel = definition.bloks
    assert (overlay.context, overlay.source, overlay.components) == ("OVERLAY", OVERLAY, None)
    assert overlay.demo_state == {"show_path": True}
    assert [local.key for local in overlay.persist] == ["show_path"]
    assert (panel.context, panel.description) == ("HTML", "The run")

    wire = definition.model_dump(mode="json", by_alias=False, exclude_unset=True)
    assert [blok["context"] for blok in wire["bloks"]] == ["OVERLAY", "HTML"]
    assert "persist" not in wire["bloks"][1], "nothing kept, nothing sent"


def test_bloks_are_not_identity_and_none_are_never_sent() -> None:
    plain = registered()
    assert definition_hash(registered(ActionBlok(OVERLAY, context="overlay", local_state={"show_path": True}))) == definition_hash(plain)
    for dump in (plain.model_dump(), plain.model_dump(mode="json", by_alias=True, exclude_none=True), plain.model_dump(mode="json")):
        assert "bloks" not in dump


def test_an_at_names_a_dependency_of_the_action() -> None:
    with pytest.raises(ValueError, match="names 'stitcher', which is not a dependency of the action"):
        registered(ActionBlok('<Overlay><At dependency="stitcher" /></Overlay>', context="overlay"))
    with pytest.raises(ValueError, match="names 'stage'"):
        registered(ActionBlok("<Overlay><Route through=\"['robot', 'stage']\" /></Overlay>", context="overlay"))


def test_a_blok_reads_only_what_the_action_has() -> None:
    registered(ActionBlok('<Card><Text text="@self.helsinki.stage" /><Text text="@robot.arm.pose" /><Text text="@args.slots" /></Card>'))
    with pytest.raises(ValueError, match="reads 'stage', which is neither a dependency of the action"):
        registered(ActionBlok('<Card><Text text="@stage.x" /></Card>'))


def test_an_overlay_is_rooted_in_the_overlay() -> None:
    with pytest.raises(ValueError, match="the root of a overlay blok must be a 'Overlay', not 'Scene'"):
        registered(ActionBlok("<Scene />", context="overlay"))


def test_one_blok_per_context_in_the_contexts_an_action_has() -> None:
    with pytest.raises(ValueError, match="the html blok is given twice"):
        registered(ActionBlok("<Card />"), ActionBlok("<Card />", context="html"))
    with pytest.raises(ValueError, match="html, scene or overlay, not logo"):
        registered(ActionBlok("<SVG />", context="logo"))


def test_locals_are_the_bloks_own() -> None:
    with pytest.raises(ValueError, match="cannot keep local state under \\['task'\\]"):
        registered(ActionBlok("<Card />", local_state={"task": None}))
    with pytest.raises(ValueError, match="cannot keep local state under \\['robot'\\]"):
        registered(ActionBlok("<Card />", local_state={"robot": None}))
    with pytest.raises(ValueError, match="cannot persist \\['zoom'\\]"):
        registered(ActionBlok("<Card />", local_state={"show_path": True}, persist=["zoom"]))


def test_only_an_actions_blok_reads_a_run() -> None:
    registry = AppRegistry()
    with pytest.raises(ValueError, match="'TaskAsk' .* reads a run of an action"):
        registry.register_blok("panel", "<Card><TaskAsk /></Card>")
    with pytest.raises(ValueError, match="an overlay is an action's own"):
        registry.register_blok("over", "<Overlay />", context="overlay")
    # A logo's Path is no Route: a logo still draws with it.
    registry.register_blok("logo", '<SVG viewBox="0 0 10 10"><Path d="M0 0L10 10" /></SVG>', context="logo")


def test_the_wire_model_checks_as_the_server_does() -> None:
    tree = ({"id": "root", "component": "Overlay"},)
    ActionBlokInput.model_validate({"context": "OVERLAY", "components": tree, "demo_state": {"show_path": True}, "persist": [{"key": "show_path"}]})
    with pytest.raises(ValidationError, match="renderer fills"):
        ActionBlokInput.model_validate({"context": "OVERLAY", "components": tree, "demo_state": {"task": {}}})
    with pytest.raises(ValidationError, match="not both"):
        ActionBlokInput.model_validate({"components": tree, "source": "<Overlay />"})
    with pytest.raises(ValidationError, match="given twice"):
        DefinitionInput.model_validate({"key": "a", "version": "1", "name": "a", "kind": "FUNCTION", "bloks": [{"source": "<Card />"}, {"source": "<Card />"}]})


@pytest.mark.parametrize(
    ("written", "keys"),
    [("['robot', 'frame']", ["robot", "frame"]), ('["robot", "frame"]', ["robot", "frame"]), ("robot, frame", ["robot", "frame"]), ("[]", [])],
)
def test_through_is_read_as_the_server_reads_it(written: str, keys: list[str]) -> None:
    assert through_keys(written) == keys
