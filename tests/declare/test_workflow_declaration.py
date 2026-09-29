"""Workflows, and what an implementation says about running it again.

- ``execution``: only a WORKFLOW may call other actions. It is resumed from its
  journal when its agent dies, pinned to its ``code_hash``.
- ``effects``: what running it again would do to the world. Purely informational;
  an app can set the default for the actions that don't say.
"""

from typing import Protocol

import pytest

from arkitekt_spec.actions import Effects, Execution
from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.declare.definition.errors import DefinitionError
from arkitekt_spec.declare.register import code_hash_of


class Lab(Protocol):
    """A lab another app runs."""

    def measure(self, n: int) -> float:
        """Measure."""
        ...


class StageState:
    """What the other app publishes about its stage."""

    x: float


class StageOnly(Protocol):
    """Only the stage's state, no actions."""

    stage: StageState


def double(x: int) -> int:
    """Double a number."""
    return 2 * x


def test_an_action_is_plain_with_unknown_effects_by_default() -> None:
    registry = AppRegistry()

    registry.register(double)

    implementation = registry.implementations["double"]
    assert (implementation.effects, implementation.execution) == (
        Effects.UNKNOWN,
        Execution.PLAIN,
    )


def test_an_action_says_what_running_it_again_would_do() -> None:
    registry = AppRegistry()

    registry.register(effects=Effects.NONE)(double)

    assert registry.implementations["double"].effects == Effects.NONE


def test_the_app_sets_the_default_and_an_actions_own_claim_wins() -> None:
    registry = AppRegistry(default_effects=Effects.IRREVERSIBLE)

    def dispense(volume: float) -> float:
        """Dispense."""
        return volume

    def read(well: str) -> float:
        """Read."""
        return 0.0

    registry.register(dispense)
    registry.register(effects=Effects.NONE)(read)

    assert registry.implementations["dispense"].effects == Effects.IRREVERSIBLE
    assert registry.implementations["read"].effects == Effects.NONE


def test_a_plain_action_may_not_call_other_actions() -> None:
    registry = AppRegistry()
    registry.declare(app="lab")(Lab)

    def use(lab: Lab, n: int) -> float:
        """Use the lab."""
        return lab.measure(n)

    with pytest.raises(DefinitionError, match="only a workflow may"):
        registry.register(use)


def test_a_workflow_may_call_other_actions() -> None:
    registry = AppRegistry()
    registry.declare(app="lab")(Lab)

    @registry.register_workflow
    def use(lab: Lab, n: int) -> float:
        """Use the lab."""
        return lab.measure(n)

    implementation = registry.implementations["use"]
    assert implementation.execution == Execution.WORKFLOW
    assert [d.key for d in implementation.dependencies] == ["lab"]


def test_a_plain_action_may_read_another_apps_state() -> None:
    registry = AppRegistry()
    registry.declare(app="stage")(StageOnly)

    def where(stage: StageOnly) -> str:
        """Where the stage is."""
        return "here"

    registry.register(where)

    assert registry.implementations["where"].execution == Execution.PLAIN


def test_the_code_hash_pins_the_body_not_the_signature() -> None:
    def a(x: int) -> int:
        """Same signature."""
        return x

    def b(x: int) -> int:
        """Same signature."""
        return x + 1

    assert code_hash_of(a) == code_hash_of(a)
    assert code_hash_of(a) != code_hash_of(b)


def test_registration_records_the_code_hash() -> None:
    registry = AppRegistry()

    registry.register(double)

    assert registry.implementations["double"].code_hash == code_hash_of(double)
