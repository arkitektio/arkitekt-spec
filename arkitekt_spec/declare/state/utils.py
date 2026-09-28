"""Decorator to register a class as a state."""

import inspect
from typing import TYPE_CHECKING, Any, get_type_hints

from arkitekt_spec.declare.actors.types import (
    AnyFunction,
    PreparedAppContextReturns,
    PreparedAppContextVariables,
    PreparedInjectedVariables,
    PreparedStateReturns,
    PreparedStateVariables,
)
from arkitekt_spec.declare.agents.types import unwrap_injectable
from arkitekt_spec.declare.definition.define import (
    get_non_null_variants,
    is_none_type,
    is_tuple,
)
from arkitekt_spec.declare.state.predicate import (
    get_read_only_state_type,
    get_state_locks,
    get_state_name,
    is_app_context,
    is_read_only_state,
    is_state,
)
from arkitekt_spec.declare.task import is_task

if TYPE_CHECKING:
    from arkitekt_spec.declare.structures.registry import StructureRegistry


def is_empty_type(cls: type) -> bool:
    """Check if the annotation is an empty type.

    Args:
        annotation (object): The annotation to check.
    Returns:
        bool: True if the annotation is an empty type, False otherwise.
    """
    return cls is inspect.Signature.empty


def get_return_length(signature: inspect.Signature) -> int:
    """Get the length of the return annotation of a function signature.

    Args:
        signature (inspect.Signature): The function signature.
    Returns:
        int: The length of the return annotation.
    """
    returns = signature.return_annotation

    if is_tuple(returns):
        return len(get_non_null_variants(returns))
    if is_none_type(returns):
        return 0
    if is_empty_type(returns):
        return 0
    else:
        return 1


def prepare_state_variables(
    function: AnyFunction, structure_registry: "StructureRegistry | None" = None
) -> tuple[PreparedStateVariables, PreparedStateReturns]:
    """Find the parameters and returns of ``function`` that are states of the app.

    A parameter is a state when its annotation is a class registered as one on
    ``structure_registry`` (``ReadOnly[...]`` for a read-only view); without a
    registry no parameter is.

    Args:
        function: The function to inspect.
        structure_registry: The app's structures, which know its states.

    Returns:
        The state variables (with the locks each requires) and the state returns.
    """
    sig = inspect.signature(function)
    parameters = sig.parameters

    try:
        hints = get_type_hints(function, include_extras=True)
    except Exception:
        hints = {}

    write_state_variables: dict[str, str] = {}
    read_only_variables: dict[str, str] = {}
    required_state_locks: dict[str, list[str]] = {}
    state_returns: dict[int, str] = {}

    for key, value in parameters.items():
        annotation = hints.get(key, value.annotation)
        if is_state(annotation, structure_registry):
            assert structure_registry is not None
            write_state_variables[key] = get_state_name(annotation, structure_registry)
            required_state_locks[key] = get_state_locks(annotation, structure_registry)
        elif is_read_only_state(annotation, structure_registry):
            assert structure_registry is not None
            # ReadOnly[SomeState] is an Annotated wrapper, so the state itself has to be
            # unwrapped before its name and locks can be read off it.
            real_state = get_read_only_state_type(annotation)
            read_only_variables[key] = get_state_name(real_state, structure_registry)
            required_state_locks[key] = get_state_locks(real_state, structure_registry)

    returns = hints.get("return", sig.return_annotation)
    if is_tuple(returns):
        for index, cls in enumerate(get_non_null_variants(returns)):
            if is_state(cls, structure_registry):
                assert structure_registry is not None
                state_returns[index] = get_state_name(cls, structure_registry)
    else:
        if is_state(returns, structure_registry):
            assert structure_registry is not None
            state_returns[0] = get_state_name(returns, structure_registry)

    return PreparedStateVariables(
        write_state_variables=write_state_variables,
        read_only_variables=read_only_variables,
        required_state_locks=required_state_locks,
    ), PreparedStateReturns(state_returns=state_returns)


def prepare_appcontext(
    function: AnyFunction, structure_registry: "StructureRegistry | None" = None
) -> tuple[PreparedAppContextVariables, PreparedAppContextReturns]:
    """The parameters and returns of ``function`` that are the app context.

    A parameter is one when its annotation is a class the app declared with
    ``@app.app_context`` on ``structure_registry``; without a registry none is.

    Args:
        function: The function to inspect.
        structure_registry: The app's structures, which hold the declaration.

    Returns:
        The app-context parameters and returns, each with its class.
    """
    sig = inspect.signature(function)
    parameters = sig.parameters

    try:
        hints = get_type_hints(function, include_extras=True)
    except Exception:
        hints = {}

    write_app_context_variables: dict[str, type[Any]] = {}
    for key, value in parameters.items():
        annotation = hints.get(key, value.annotation)
        if is_app_context(annotation, structure_registry):
            write_app_context_variables[key] = annotation

    returns = hints.get("return", sig.return_annotation)

    app_context_returns: dict[int, type[Any]] = {}

    if is_tuple(returns):
        for index, cls in enumerate(get_non_null_variants(returns)):
            if is_app_context(cls, structure_registry):
                app_context_returns[index] = cls
    else:
        if is_app_context(returns, structure_registry):
            app_context_returns[0] = returns

    return PreparedAppContextVariables(
        app_context_variables=write_app_context_variables,
    ), PreparedAppContextReturns(app_context_returns=app_context_returns)


def prepare_injected_variables(
    function: AnyFunction, structure_registry: "StructureRegistry | None" = None
) -> PreparedInjectedVariables:
    """Find the parameters of a function that ask for a client (``mikro: Mikro``),
    for the task it runs for, or for the app context the run was started with.

    A parameter asks for a client when its annotation is what a service registered
    on ``structure_registry`` returns, and for the app context when it is the class
    the app declared as such; without a registry neither is. Hooks read the app
    context through :func:`prepare_appcontext`; this is the action's side of it.
    """
    sig = inspect.signature(function)

    try:
        hints = get_type_hints(function, include_extras=True)
    except Exception:
        hints = {}

    annotations = {
        key: hints.get(key, value.annotation) for key, value in sig.parameters.items()
    }

    return PreparedInjectedVariables(
        service_client_variables={
            key: unwrap_injectable(annotation)
            for key, annotation in annotations.items()
            if structure_registry is not None and structure_registry.is_client(annotation)
        },
        task_variables=[
            key for key, annotation in annotations.items() if is_task(annotation)
        ],
        app_context_variables={
            key: annotation
            for key, annotation in annotations.items()
            if is_app_context(annotation, structure_registry)
        },
    )
