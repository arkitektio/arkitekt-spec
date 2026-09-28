"""Validation tests for the assembled ``ImplementAgentInput``.

The payload-level trait resolves ``self`` state choice references against the
agent's own states and enforces structural invariants (unique interfaces, lock
references) that a single ``ImplementationInput`` cannot see.
"""

from dataclasses import dataclass

import pytest

from arkitekt_spec.actions import (
    AgentDependencyInput,
    ImplementAgentInput,
    ImplementationInput,
    PortKind,
    ReturnPortInput,
    StateDefinitionInput,
    StateDemandInput,
    StateDependencyInput,
    StateImplementationInput,
)
from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.declare.definition.checks import check_agent_input
from arkitekt_spec.declare.definition.define import prepare_definition
from arkitekt_spec.declare.definition.match import build_port_matches
from arkitekt_spec.declare.register import RegisterConfig, register_func
from arkitekt_spec.declare.structures.registry import StructureRegistry
from arkitekt_spec.declare.widgets import withStateChoices


def _register_camera_state(app: AppRegistry) -> None:
    """Register a CameraState into the given app registry via the real machinery."""

    @dataclass
    class CameraState:
        exposure_ms: float = 0.0

    app.state(name="CameraState")(CameraState)


def _camera_state() -> StateImplementationInput:
    return StateImplementationInput(
        interface="camera_state",
        definition=StateDefinitionInput(
            name="CameraState",
            ports=(
                ReturnPortInput(
                    key="exposure_ms",
                    kind=PortKind.FLOAT,
                    nullable=False,
                ),
            ),
        ),
    )


def _implementation(
    simple_registry: StructureRegistry,
    *,
    interface: str = "adjust",
    state_path: str | None = None,
    dependencies: tuple[AgentDependencyInput, ...] = (),
    locks: tuple[str, ...] = (),
) -> ImplementationInput:
    def adjust(exposure: float) -> None:
        """Adjust the exposure of the camera."""

    widgets = {"exposure": withStateChoices(state_path)} if state_path else None
    definition = prepare_definition(
        adjust,
        structure_registry=simple_registry,
        widgets=widgets,
    )
    return ImplementationInput(
        definition=definition,
        dependencies=dependencies,
        interface=interface,
        locks=locks or None,
        needs_token=True,
    )


def test_self_state_choice_resolving_against_own_state_is_accepted(
    simple_registry: StructureRegistry,
) -> None:
    agent_input = ImplementAgentInput(
        implementations=(
            _implementation(
                simple_registry, state_path="self.camera_state.exposure_ms"
            ),
        ),
        states=(_camera_state(),),
    )
    assert agent_input.implementations is not None


def test_self_state_choice_without_own_state_is_rejected(
    simple_registry: StructureRegistry,
) -> None:
    with pytest.raises(ValueError, match="camera_state"):
        check_agent_input(ImplementAgentInput(
            implementations=(
                _implementation(
                    simple_registry, state_path="self.camera_state.exposure_ms"
                ),
            ),
            states=(),
        ))


def test_self_state_choice_with_unknown_field_is_rejected(
    simple_registry: StructureRegistry,
) -> None:
    with pytest.raises(ValueError, match="missing_field"):
        check_agent_input(ImplementAgentInput(
            implementations=(
                _implementation(
                    simple_registry, state_path="self.camera_state.missing_field"
                ),
            ),
            states=(_camera_state(),),
        ))


def test_duplicate_implementation_interface_is_rejected(
    simple_registry: StructureRegistry,
) -> None:
    with pytest.raises(ValueError, match="Duplicate implementation interface"):
        check_agent_input(ImplementAgentInput(
            implementations=(
                _implementation(simple_registry, interface="dup"),
                _implementation(simple_registry, interface="dup"),
            ),
        ))


def test_lock_reference_without_lock_implementation_is_rejected(
    simple_registry: StructureRegistry,
) -> None:
    with pytest.raises(ValueError, match="references lock 'mylock'"):
        check_agent_input(ImplementAgentInput(
            implementations=(_implementation(simple_registry, locks=("mylock",)),),
        ))


def test_real_registration_path_self_widget_validates(
    simple_registry: StructureRegistry,
) -> None:
    # End-to-end: register a state and a function whose widget references that
    # state via "self", then assemble + validate through the app registry.
    app = AppRegistry()
    _register_camera_state(app)

    def adjust(exposure: float) -> None:
        """Adjust the exposure of the camera."""

    register_func(
        adjust,
        app.structure_registry,
        app,
        RegisterConfig(
            widgets={"exposure": withStateChoices("self.CameraState.exposure_ms")},
            stateful=True,
        ),
    )

    agent_input = app.to_implement_agent_input("inst")
    assert "CameraState" in {state.interface for state in agent_input.states or ()}


def test_real_registration_path_self_widget_unknown_field_is_rejected(
    simple_registry: StructureRegistry,
) -> None:
    app = AppRegistry()
    _register_camera_state(app)

    def adjust(exposure: float) -> None:
        """Adjust the exposure of the camera."""

    register_func(
        adjust,
        app.structure_registry,
        app,
        RegisterConfig(
            widgets={"exposure": withStateChoices("self.CameraState.missing_field")},
            stateful=True,
        ),
    )

    with pytest.raises(ValueError, match="missing_field"):
        app.to_implement_agent_input("inst")


def test_externally_dependent_agent_is_accepted(
    simple_registry: StructureRegistry,
) -> None:
    # An implementation that depends on a REMOTE camera agent (and provides no
    # camera state of its own, and has no self widget) must validate fine. This
    # guards against treating dependency demands as "must be provided locally".
    camera_dependency = AgentDependencyInput(
        key="camera",
        optional=False,
        auto_resolvable=False,
        state_dependencies=(
            StateDependencyInput(
                key="state",
                optional=False,
                demand=StateDemandInput(
                    matches=build_port_matches(
                        (
                            ReturnPortInput(
                                key="exposure_ms",
                                kind=PortKind.FLOAT,
                                nullable=False,
                            ),
                        )
                    ),
                ),
            ),
        ),
    )

    agent_input = ImplementAgentInput(
        implementations=(
            _implementation(simple_registry, dependencies=(camera_dependency,)),
        ),
        states=(),
    )
    assert agent_input.implementations is not None
