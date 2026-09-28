"""Declaring startup, background and shutdown hooks.

A hook is declared here: its signature is analysed (which app context, states,
contexts and clients it takes; what it returns) and the declaration is recorded on the
app's :class:`~arkitekt_spec.declare.agents.hooks.registry.HooksRegistry`. Running it
-- in the event loop, or in a worker thread for a synchronous hook -- is the runtime's:
rekuest wraps each declaration in a runner when its agent starts.
"""

import inspect
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar, get_type_hints, overload

from arkitekt_spec.declare.agents.hooks.registry import HooksRegistry
from arkitekt_spec.declare.agents.hooks.variables import WithVariables
from arkitekt_spec.declare.agents.types import BoundApp
from arkitekt_spec.declare.definition.define import is_none_type
from arkitekt_spec.declare.protocol.types import (
    AnyFunction,
    BackgroundFunction,
    ContextLessStartupFunction,
    ShutdownFunction,
    StartupFunction,
)
from arkitekt_spec.declare.state.utils import get_return_length, is_empty_type

if TYPE_CHECKING:
    from arkitekt_spec.declare.structures.registry import StructureRegistry


class StartupWithVariables(WithVariables):
    """Startup hooks run before any state or context exists: only the app context is injectable."""

    hook_kind = "Startup"
    injects_states = False

    def get_startup_kwargs(self, app_context: Any, bound_app: BoundApp | None) -> dict[str, Any]:
        """Bind the app context and the app by parameter name.

        By name rather than by position, so one hook can take both. No state or
        context exists yet, hence the empty mappings.
        """
        return self.get_kwargs({}, {}, app_context, bound_app=bound_app)

    def validate_returns(self, func: AnyFunction) -> None:
        allowed_return_types = self.state_returns.count + self.context_returns.count
        if get_return_length(inspect.signature(func)) > allowed_return_types:
            raise ValueError(
                f"Startup function {func.__name__} has more return values than the context and state variables. "
                f"Expected at most {allowed_return_types} return values, but got {get_return_length(inspect.signature(func))}."
            )


class BackgroundWithVariables(WithVariables):
    hook_kind = "Background"


class ShutdownWithVariables(WithVariables):
    """Shutdown hooks may take anything but must not return: the agent is tearing down."""

    hook_kind = "Shutdown"

    def validate_returns(self, func: AnyFunction) -> None:
        # Resolve the hints first: an unresolved ``-> None`` annotation is the literal
        # None, which get_return_length would count as a return value.
        try:
            hints = get_type_hints(func, include_extras=True)
        except Exception:
            hints = {}
        returns = hints.get("return", inspect.signature(func).return_annotation)

        if not (is_none_type(returns) or is_empty_type(returns)):
            raise ValueError(
                f"Shutdown function {func.__name__} must not return anything, but returns {returns}. "
                "The agent is tearing down, so returned states and contexts would never be used."
            )


class _Declared:
    """What every hook declaration keeps besides its analysis."""

    func: AnyFunction
    structure_registry: "StructureRegistry | None"

    @property
    def threaded(self) -> bool:
        """Whether the hook is synchronous, so a runtime runs it in a worker thread."""
        return not (
            inspect.iscoroutinefunction(self.func) or inspect.isasyncgenfunction(self.func)
        )


class StartupHookDeclaration(_Declared, StartupWithVariables):
    """A declared startup hook."""

    def __init__(
        self, func: AnyFunction, structure_registry: "StructureRegistry | None" = None
    ) -> None:
        """Analyse ``func`` as a startup hook."""
        super().__init__(func, structure_registry)
        self.structure_registry = structure_registry


class BackgroundDeclaration(_Declared, BackgroundWithVariables):
    """A declared background worker."""

    def __init__(
        self, func: AnyFunction, structure_registry: "StructureRegistry | None" = None
    ) -> None:
        """Analyse ``func`` as a background worker."""
        super().__init__(func, structure_registry)
        self.structure_registry = structure_registry


class ShutdownHookDeclaration(_Declared, ShutdownWithVariables):
    """A declared shutdown hook."""

    def __init__(
        self, func: AnyFunction, structure_registry: "StructureRegistry | None" = None
    ) -> None:
        """Analyse ``func`` as a shutdown hook."""
        super().__init__(func, structure_registry)
        self.structure_registry = structure_registry


TStartup = TypeVar("TStartup", bound=StartupFunction | ContextLessStartupFunction)
TBackground = TypeVar("TBackground", bound=BackgroundFunction)
TShutdown = TypeVar("TShutdown", bound=ShutdownFunction)


def _require_function(function: Any) -> None:
    if not (
        inspect.iscoroutinefunction(function)
        or inspect.isfunction(function)
        or inspect.ismethod(function)
    ):
        raise TypeError("A hook must be an async function or a sync function")


@overload
def declare_startup(
    func: TStartup,
    /,
    *,
    name: str | None = None,
    registry: HooksRegistry,
    structure_registry: "StructureRegistry | None" = None,
) -> TStartup: ...


@overload
def declare_startup(
    func: None = None,
    /,
    *,
    name: str | None = None,
    registry: HooksRegistry,
    structure_registry: "StructureRegistry | None" = None,
) -> Callable[[TStartup], TStartup]: ...


def declare_startup(
    func: TStartup | None = None,
    /,
    *,
    name: str | None = None,
    registry: HooksRegistry,
    structure_registry: "StructureRegistry | None" = None,
) -> TStartup | Callable[[TStartup], TStartup]:
    """Declare a startup hook: run when the app starts, returning its initial states and contexts.

    Reached through ``AppRegistry.startup``; call it directly only with ``registry=``.
    """

    def decorator(function: TStartup) -> TStartup:
        _require_function(function)
        registry.register_startup(
            name or function.__name__, StartupHookDeclaration(function, structure_registry)
        )
        return function

    return decorator(func) if func is not None else decorator


@overload
def declare_background(
    func: TBackground,
    /,
    *,
    name: str | None = None,
    registry: HooksRegistry,
    structure_registry: "StructureRegistry | None" = None,
) -> TBackground: ...


@overload
def declare_background(
    func: None = None,
    /,
    *,
    name: str | None = None,
    registry: HooksRegistry,
    structure_registry: "StructureRegistry | None" = None,
) -> Callable[[TBackground], TBackground]: ...


def declare_background(
    func: TBackground | None = None,
    /,
    *,
    name: str | None = None,
    registry: HooksRegistry,
    structure_registry: "StructureRegistry | None" = None,
) -> TBackground | Callable[[TBackground], TBackground]:
    """Declare a background worker: run alongside the app for as long as it runs.

    Reached through ``AppRegistry.background``; call it directly only with ``registry=``.
    """

    def decorator(function: TBackground) -> TBackground:
        _require_function(function)
        registry.register_background(
            name or function.__name__, BackgroundDeclaration(function, structure_registry)
        )
        return function

    return decorator(func) if func is not None else decorator


@overload
def declare_shutdown(
    func: TShutdown,
    /,
    *,
    name: str | None = None,
    registry: HooksRegistry,
    structure_registry: "StructureRegistry | None" = None,
) -> TShutdown: ...


@overload
def declare_shutdown(
    func: None = None,
    /,
    *,
    name: str | None = None,
    registry: HooksRegistry,
    structure_registry: "StructureRegistry | None" = None,
) -> Callable[[TShutdown], TShutdown]: ...


def declare_shutdown(
    func: TShutdown | None = None,
    /,
    *,
    name: str | None = None,
    registry: HooksRegistry,
    structure_registry: "StructureRegistry | None" = None,
) -> TShutdown | Callable[[TShutdown], TShutdown]:
    """Declare a shutdown hook: run when the app tears down, in reverse order.

    Reached through ``AppRegistry.shutdown``; call it directly only with ``registry=``.
    """

    def decorator(function: TShutdown) -> TShutdown:
        _require_function(function)
        registry.register_shutdown(
            name or function.__name__, ShutdownHookDeclaration(function, structure_registry)
        )
        return function

    return decorator(func) if func is not None else decorator
