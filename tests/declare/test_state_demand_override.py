"""Tests for the ``demand_state`` marker on agent-dependency protocol attributes.

A declared protocol's public annotated attributes become state demands. By
default a state demand inherits its ``app`` from the protocol's core app and its
``key`` from the attribute name. An :func:`demand_state` marker (placed in
``typing.Annotated``) redirects it to *another* state.
"""

from typing import Annotated, Any

from arkitekt_spec.actions import (
    AgentDependencyInput,
    StateDemandInput,
)
from arkitekt_spec.declare.declare import demand_state
from arkitekt_spec.declare.structures.registry import StructureRegistry


def _dependency_of(cls: Any) -> AgentDependencyInput:
    """Declare ``cls`` on an app directed at ``mymicroscope`` and read the demand back."""
    registry = StructureRegistry()
    registry.declare(app="mymicroscope")(cls)
    return registry.protocol_for(cls).to_dependency_input("dep")


def _state_demand_for(
    dependency: AgentDependencyInput, slot_key: str
) -> StateDemandInput:
    (match,) = [d for d in dependency.state_dependencies or () if d.key == slot_key]
    assert match.demand is not None
    return match.demand


def _state_slot_for(dependency: AgentDependencyInput, slot_key: str):
    (match,) = [d for d in dependency.state_dependencies or () if d.key == slot_key]
    return match


def test_state_demand_inherits_app_and_attr_key_by_default() -> None:
    class CameraState:
        connected: bool

    class Deps:
        camera: CameraState

    dependency = _dependency_of(Deps)
    demanded = _state_demand_for(dependency, "camera")

    assert demanded.app == "mymicroscope"
    assert demanded.key == "camera"
    assert demanded.matches is not None


def test_demand_state_overrides_app_and_key_to_another_state() -> None:
    class ViewerState:
        open: bool

    class Deps:
        viewer: Annotated[ViewerState, demand_state(app="imagej", key="viewer_state")]

    dependency = _dependency_of(Deps)
    # Slot key stays the attribute name so assignments keep referencing "viewer"...
    demanded = _state_demand_for(dependency, "viewer")
    # ...but the demanded state now points at a different app + key.
    assert demanded.app == "imagej"
    assert demanded.key == "viewer_state"


def test_demand_state_can_pin_by_hash_and_disable_port_matching() -> None:
    class StatusState:
        ready: bool

    class Deps:
        status: Annotated[StatusState, demand_state(hash="abc123", match_ports=False)]

    dependency = _dependency_of(Deps)
    demanded = _state_demand_for(dependency, "status")

    assert demanded.hash == "abc123"
    assert demanded.matches is None


def test_mixed_default_and_overridden_states_coexist() -> None:
    class CameraState:
        connected: bool

    class ViewerState:
        open: bool

    class Deps:
        camera: CameraState
        viewer: Annotated[ViewerState, demand_state(app="imagej", key="viewer_state")]

    dependency = _dependency_of(Deps)

    assert _state_demand_for(dependency, "camera").app == "mymicroscope"
    assert _state_demand_for(dependency, "camera").key == "camera"
    assert _state_demand_for(dependency, "viewer").app == "imagej"
    assert _state_demand_for(dependency, "viewer").key == "viewer_state"


def test_state_slots_are_required_by_default_and_optional_when_marked() -> None:
    class CameraState:
        connected: bool

    class TelemetryState:
        uptime: float

    class Deps:
        camera: CameraState
        telemetry: Annotated[TelemetryState, demand_state(optional=True)]

    dependency = _dependency_of(Deps)

    assert _state_slot_for(dependency, "camera").optional is False
    assert _state_slot_for(dependency, "telemetry").optional is True
