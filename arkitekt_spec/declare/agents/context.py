"""Contexts: what an agent holds for its lifetime, handed out by annotation.

A context class is declared on an app (``@app.context``); what the app knows
about it -- its name and the locks its use requires -- lives on that app's
structure registry, and nothing is written on the class. A startup hook
returning one publishes it; an action or hook annotated with it is handed it.
"""

import inspect
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, get_type_hints

from arkitekt_spec.declare.definition.define import get_non_null_variants, is_tuple
from arkitekt_spec.declare.protocol.types import AnyFunction

if TYPE_CHECKING:
    from arkitekt_spec.declare.structures.registry import StructureRegistry


def is_context(annotation: Any, structure_registry: "StructureRegistry | None") -> bool:
    """Whether ``annotation`` is a class the app declared as a context.

    Without a registry nothing is: being a context is a declaration on an app,
    not a property of the class.
    """
    return structure_registry is not None and structure_registry.is_context(annotation)


def get_context_name(cls: Any, structure_registry: "StructureRegistry") -> str:
    """The name the app keeps the context ``cls`` under."""
    return structure_registry.context_for(cls).name


def get_context_locks(cls: Any, structure_registry: "StructureRegistry") -> list[str]:
    """The locks the app requires while a parameter of ``cls`` is held."""
    return list(structure_registry.context_for(cls).locks)


@dataclass
class PreparedContextVariables:
    context_variables: dict[str, str]
    required_context_locks: dict[str, list[str]]

    @property
    def count(self) -> int:
        """Get the amount of state variables."""
        return len(self.context_variables)


@dataclass
class PreparedContextReturns:
    context_returns: dict[int, str]

    @property
    def count(self) -> int:
        """Get the amount of context variables."""
        return len(self.context_returns)


def prepare_context_variables(
    function: AnyFunction, structure_registry: "StructureRegistry | None" = None
) -> tuple[PreparedContextVariables, PreparedContextReturns]:
    """The context parameters and returns of ``function``, by name and index.

    A parameter is a context when its annotation is a class the app declared
    one on ``structure_registry``; without a registry no parameter is.

    Args:
        function: The function to inspect.
        structure_registry: The app's structures, which hold its contexts.

    Returns:
        The context parameters (with their locks) and the context returns.
    """
    sig = inspect.signature(function)
    parameters = sig.parameters

    try:
        hints = get_type_hints(function, include_extras=True)
    except Exception:
        hints = {}

    state_variables: dict[str, str] = {}
    state_returns: dict[int, str] = {}
    required_locks: dict[str, list[str]] = {}

    for key, value in parameters.items():
        cls = hints.get(key, value.annotation)
        if is_context(cls, structure_registry):
            assert structure_registry is not None
            state_variables[key] = get_context_name(cls, structure_registry)
            required_locks[key] = get_context_locks(cls, structure_registry)

    returns = hints.get("return", sig.return_annotation)

    if is_tuple(returns):
        for index, cls in enumerate(get_non_null_variants(returns)):
            if is_context(cls, structure_registry):
                assert structure_registry is not None
                state_returns[index] = get_context_name(cls, structure_registry)
    else:
        if is_context(returns, structure_registry):
            assert structure_registry is not None
            state_returns[0] = get_context_name(returns, structure_registry)

    return (
        PreparedContextVariables(
            context_variables=state_variables, required_context_locks=required_locks
        ),
        PreparedContextReturns(context_returns=state_returns),
    )
