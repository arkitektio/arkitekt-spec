"""A blok's persisted locals: the local state a renderer may keep between openings, on the device."""

import pytest

from arkitekt_spec.actions import BlokImplementationInput, BlokPersistInput
from arkitekt_spec.declare.app import AppRegistry

POSED = '<Text text="pose" />'


def test_persisted_locals_reach_the_wire() -> None:
    registry = AppRegistry()
    registry.register_blok("posed", POSED, local_state={"pose": [1, 0, 0, 0], "zoom": 1}, persist={"pose": "Where the gizmo left it"})

    wire = registry.to_implement_agent_input().model_dump(mode="json", by_alias=False, exclude_unset=True)
    (blok,) = wire["bloks"]
    assert blok["persist"] == [{"key": "pose", "description": "Where the gizmo left it"}]
    assert blok["demo_state"]["pose"] == [1, 0, 0, 0]


def test_a_list_of_keys_persists_without_descriptions() -> None:
    registry = AppRegistry()
    registry.register_blok("posed", POSED, local_state={"pose": None, "zoom": 1}, persist=["pose", "zoom"])
    blok = registry.get_declared_bloks()["posed"]
    assert blok.persist == (BlokPersistInput(key="pose"), BlokPersistInput(key="zoom"))


def test_a_blok_that_keeps_nothing_sends_what_it_always_sent() -> None:
    """Never dumped when empty, in any dump: older strict readers accept it and its hash does not move."""
    registry = AppRegistry()
    registry.register_blok("panel", POSED, local_state={"pose": None})
    agent = registry.to_implement_agent_input()
    for dump in (
        agent.model_dump(mode="json", by_alias=False, exclude_unset=True),
        agent.model_dump(mode="json", by_alias=True, exclude_none=True),
        agent.model_dump(mode="json"),
    ):
        assert "persist" not in dump["bloks"][0]


def test_only_a_local_is_persisted() -> None:
    registry = AppRegistry()
    with pytest.raises(ValueError, match=r"cannot persist \['speed'\]"):
        registry.register_blok("posed", POSED, local_state={"pose": None}, persist=["speed"])


def test_the_wire_model_checks_as_the_server_does() -> None:
    blok = {"key": "posed", "components": ({"id": "root", "component": "Box"},), "demo_state": {"pose": None}}
    BlokImplementationInput.model_validate({**blok, "persist": [{"key": "pose"}]})
    with pytest.raises(ValueError, match="persist names 'speed', which is not a demo_state key"):
        BlokImplementationInput.model_validate({**blok, "persist": [{"key": "speed"}]})
    with pytest.raises(ValueError, match="duplicate key 'pose'"):
        BlokImplementationInput.model_validate({**blok, "persist": [{"key": "pose"}, {"key": "pose"}]})
    with pytest.raises(ValueError, match="not a local but a root the renderer fills"):
        BlokImplementationInput.model_validate({**blok, "demo_state": {"args": {}}, "persist": [{"key": "args"}]})
