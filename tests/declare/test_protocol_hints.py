"""Protocol hints: an action names where it means to hook in, and nothing more.

A hint is a name a caller filters on (``on_scene_annotation``). It is not a
contract -- nothing checks the ports against it -- and not identity: an action
that gains or loses a hint is the same action, with the same hash. An action
that hints nothing must register exactly as it did before hints existed.
"""

import pytest
from pydantic import ValidationError

from arkitekt_spec.actions import DefinitionInput, definition_hash
from arkitekt_spec.declare.app import AppRegistry


def segment(path: str, tolerance: float = 0.35) -> str:
    """Segment along a path."""
    return path


def definition(**options: object) -> DefinitionInput:
    registry = AppRegistry()
    registry.register_action(segment, **options)
    return registry.implementations["segment"].definition


def test_hints_reach_the_definition_in_order() -> None:
    assert definition(protocols=["on_scene_annotation", "on_path"]).protocols == (
        "on_scene_annotation",
        "on_path",
    )


def test_register_forwards_them_too() -> None:
    registry = AppRegistry()
    registry.register(protocols=["on_scene_init"])(segment)
    assert registry.implementations["segment"].definition.protocols == ("on_scene_init",)


def test_a_hint_is_not_identity() -> None:
    assert definition_hash(definition(protocols=["on_scene_annotation"])) == definition_hash(definition())


def test_without_hints_nothing_new_goes_on_the_wire() -> None:
    """Not on the socket, not in a release descriptor, not in an agent's hash: every
    strict reader that predates hints (an older takt, kabinet's import) must accept it."""
    plain = definition()
    assert "protocols" not in plain.model_dump()
    assert "protocols" not in plain.model_dump(mode="json", by_alias=True)
    assert "protocols" not in definition(protocols=[]).model_dump()
    hinted = definition(protocols=["on_scene_annotation"])
    assert hinted.model_dump(exclude_unset=True)["protocols"] == ("on_scene_annotation",)


@pytest.mark.parametrize("bad", ["", "on scene", "on_scene\t"])
def test_a_hint_is_one_word(bad: str) -> None:
    with pytest.raises(ValidationError, match="protocol hint"):
        definition(protocols=[bad])


def test_a_hint_is_given_once() -> None:
    with pytest.raises(ValidationError, match="given twice"):
        definition(protocols=["on_scene_init", "on_scene_init"])
