"""Context management for Rekuest."""

import inspect
from typing import TYPE_CHECKING, Any

from arkitekt_spec.declare.actors.types import PreparedDependencyVariables
from arkitekt_spec.declare.declare import DeclaredAgentProtocol
from arkitekt_spec.declare.definition.define import (
    get_non_null_variants,
    is_dependency_type,
    is_tuple,
)
from arkitekt_spec.declare.protocol.types import AnyFunction

if TYPE_CHECKING:
    from arkitekt_spec.declare.structures.registry import StructureRegistry


def prepare_dependency_variables(
    function: AnyFunction, structure_registry: "StructureRegistry | None" = None
) -> PreparedDependencyVariables:
    """Find the parameters of ``function`` annotated with a declared protocol.

    A parameter is a dependency when its annotation is a class the app declared
    a protocol for on ``structure_registry``; without a registry no parameter is.
    A dependency may not be returned, as a tuple member or on its own.

    Args:
        function: The function to inspect.
        structure_registry: The declaring app's structures.

    Returns:
        The dependency parameters, by name.

    Raises:
        NotImplementedError: If the return annotation names a dependency.
    """
    sig = inspect.signature(function)
    parameters = sig.parameters

    depedency_variables: dict[str, str] = {}

    for key, value in parameters.items():
        cls = value.annotation
        if is_dependency_type(cls, structure_registry):
            depedency_variables[key] = cls

    returns = sig.return_annotation

    if hasattr(returns, "_name"):
        if is_tuple(returns):
            for _, cls in enumerate(get_non_null_variants(returns)):
                if is_dependency_type(cls, structure_registry):
                    raise NotImplementedError(
                        "Dependency variables cannot be returned as tuples."
                    )
        else:
            if is_dependency_type(returns, structure_registry):
                raise NotImplementedError(
                    "Dependency variables cannot be returned as single values."
                )
    return PreparedDependencyVariables(dependency_variables=depedency_variables)


def dependency_to_protocol(
    cls: Any, structure_registry: "StructureRegistry"
) -> DeclaredAgentProtocol[Any]:
    """The protocol the app declared for ``cls``."""
    return structure_registry.protocol_for(cls)
