"""Turning a function into its definition and implementation details.

Pure analysis of the function's signature, run once at declaration. Building the
actor that executes the function is a runtime's job (rekuest's ``reactify``).
"""

from typing import Any

from arkitekt_spec.actions import DefinitionInput
from arkitekt_spec.declare.actors.types import ImplementationDetails, RegisterConfig
from arkitekt_spec.declare.agents.context import prepare_context_variables
from arkitekt_spec.declare.agents.dependency import prepare_dependency_variables
from arkitekt_spec.declare.definition.define import prepare_definition
from arkitekt_spec.declare.protocol.types import AnyFunction
from arkitekt_spec.declare.state.utils import (
    prepare_injected_variables,
    prepare_state_variables,
)
from arkitekt_spec.declare.structures.registry import StructureRegistry


def derive_implementation_details(
    function: AnyFunction,
    config: RegisterConfig,
    structure_registry: StructureRegistry | None = None,
) -> ImplementationDetails:
    """Inspect a function's state/context/dependency variables and resolve the
    implementation metadata.

    Explicit ``config.locks``/``config.manipulates`` win; otherwise locks are
    inferred from the required state/context locks (when ``config.auto_locks``)
    and manipulates from the written state variables.
    """
    state_variables, state_returns = prepare_state_variables(function, structure_registry)
    context_variables, context_returns = prepare_context_variables(function, structure_registry)
    dependency_variables = prepare_dependency_variables(function, structure_registry)
    injected_variables = prepare_injected_variables(function, structure_registry)

    locks = config.locks
    if locks is None and config.auto_locks:
        newlocks: list[str] = []
        for lock in context_variables.required_context_locks.values():
            newlocks.extend(lock)
        for lock in state_variables.required_state_locks.values():
            newlocks.extend(lock)
        # Sorted so the definition (and its hash) is identical across processes;
        # ``set`` order depends on the hash seed.
        locks = sorted(set(newlocks))

    manipulates = config.manipulates
    if manipulates is None:
        manipulates = sorted(set(state_variables.write_state_variables.values()))

    return ImplementationDetails(
        state_variables=state_variables,
        state_returns=state_returns,
        context_variables=context_variables,
        context_returns=context_returns,
        dependency_variables=dependency_variables,
        locks=locks,
        tracks=config.tracks,
        manipulates=manipulates,
        injected_variables=injected_variables,
    )


def prepare_definition_from_config(
    function: AnyFunction,
    structure_registry: StructureRegistry,
    config: RegisterConfig,
    details: ImplementationDetails | None = None,
    **prepare_overrides: Any,
) -> DefinitionInput:
    """Build the definition for a function from its bundled RegisterConfig.

    ``details`` (when given) marks the definition stateful if the function
    uses state variables. ``prepare_overrides`` are forwarded to
    ``prepare_definition`` (e.g. ``omitfirst`` for the Qt actifiers).
    """
    stateful = config.stateful or bool(details and details.state_variables.count)

    return prepare_definition(
        function,
        structure_registry,
        widgets=config.widgets,
        port_groups=config.port_groups,
        collections=config.collections,
        stateful=stateful,
        validators=config.validators,
        port_effects=config.port_effects,
        is_test_for=config.is_test_for,
        name=config.name,
        description=config.description,
        return_widgets=config.return_widgets,
        key=config.key,
        version=config.version,
        catalogs=config.catalogs,
        **prepare_overrides,
    )
