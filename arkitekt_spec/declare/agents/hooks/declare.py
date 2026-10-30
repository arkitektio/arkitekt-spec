"""Declaring startup, background and shutdown hooks.

A hook is declared here: its signature is analysed (which app context, states,
contexts and clients it takes; what it returns) and the declaration is recorded on the
app's :class:`~arkitekt_spec.declare.agents.hooks.registry.HooksRegistry`. Running it
-- in the event loop, or in a worker thread for a synchronous hook -- is the runtime's:
rekuest wraps each declaration in a runner when its agent starts.
"""

import collections.abc
import inspect
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar, get_args, get_origin, get_type_hints, overload

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
from arkitekt_spec.declare.state.utils import (
    get_return_length,
    is_empty_type,
    prepare_previous_state_variables,
)

if TYPE_CHECKING:
    from arkitekt_spec.declare.structures.registry import StructureRegistry


class StartupWithVariables(WithVariables):
    """Startup hooks run before any state or context exists: only the app context is
    injectable, and what an earlier run persisted of a state (``SomeState | None``)."""

    hook_kind = "Startup"
    injects_states = False

    def __init__(
        self, func: AnyFunction, structure_registry: "StructureRegistry | None" = None
    ) -> None:
        self.previous_state_variables = prepare_previous_state_variables(func, structure_registry)
        super().__init__(func, structure_registry)

    def _validate_arguments(self, func: AnyFunction) -> None:
        if self.state_variables.count:
            raise ValueError(
                f"Startup function {func.__name__} takes the state "
                f"{list(self.state_variables.variable_keys)}, but no state exists yet when it "
                "runs. To read what an earlier run persisted, annotate it `SomeState | None` "
                "and declare the state with @app.state(persist=...)."
            )
        super()._validate_arguments(func)

    def get_startup_kwargs(
        self,
        app_context: Any,
        bound_app: BoundApp | None,
        previous_states: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Bind the app context, the app and earlier runs' states by parameter name.

        By name rather than by position, so one hook can take them all. No state or
        context exists yet, hence the empty mappings.
        """
        kwargs = self.get_kwargs({}, {}, app_context, bound_app=bound_app)
        for key, interface in self.previous_state_variables.items():
            kwargs[key] = (previous_states or {}).get(interface)
        return kwargs

    def validate_returns(self, func: AnyFunction) -> None:
        allowed_return_types = self.state_returns.count + self.context_returns.count
        if get_return_length(inspect.signature(func)) > allowed_return_types:
            raise ValueError(
                f"Startup function {func.__name__} has more return values than the context and state variables. "
                f"Expected at most {allowed_return_types} return values, but got {get_return_length(inspect.signature(func))}."
            )


def _yields_as_returns(func: AnyFunction) -> AnyFunction:
    """A stand-in for an async generator, whose return annotation is what it yields.

    What a lifecycle hook yields sets up the same states and contexts a startup hook's
    return does, so the same analysis reads it.
    """
    try:
        hints = get_type_hints(func, include_extras=True)
    except Exception as e:
        raise ValueError(
            f"Lifecycle function {func.__name__} has annotations that cannot be resolved "
            f"({e}). What it takes and yields is read from them: they must name classes "
            "visible from its module, not local ones under `from __future__ import annotations`."
        ) from e

    yields: Any = inspect.Signature.empty
    if "return" in hints:
        returns = hints.pop("return")
        # A bare `collections.abc.AsyncIterator` has no origin: it is one itself.
        if (get_origin(returns) or returns) not in (
            collections.abc.AsyncIterator,
            collections.abc.AsyncGenerator,
            collections.abc.AsyncIterable,
        ):
            raise ValueError(
                f"Lifecycle function {func.__name__} is annotated {returns}. Annotate what it "
                "yields as AsyncIterator[SomeContext], or AsyncIterator[tuple[SomeContext, SomeState]]."
            )
        args = get_args(returns)
        # `typing.AsyncIterator[None]` holds NoneType, `collections.abc`'s holds None.
        if args and args[0] is not None and args[0] is not type(None):
            yields = args[0]

    def stand_in() -> None: ...

    stand_in.__name__ = func.__name__
    stand_in.__signature__ = inspect.signature(func).replace(return_annotation=yields)  # type: ignore[attr-defined]
    stand_in.__annotations__ = hints if yields is inspect.Signature.empty else {**hints, "return": yields}
    return stand_in


class LifecycleWithVariables(StartupWithVariables):
    """A startup hook and its shutdown in one async generator: what it yields sets up the
    app's states and contexts, and what follows the yield runs when the app tears down."""

    hook_kind = "Lifecycle"

    def __init__(
        self, func: AnyFunction, structure_registry: "StructureRegistry | None" = None
    ) -> None:
        super().__init__(_yields_as_returns(func), structure_registry)
        self.func = func


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


class LifecycleHookDeclaration(_Declared, LifecycleWithVariables):
    """A declared lifecycle hook."""

    def __init__(
        self, func: AnyFunction, structure_registry: "StructureRegistry | None" = None
    ) -> None:
        """Analyse ``func`` as a lifecycle hook."""
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
TLifecycle = TypeVar("TLifecycle", bound=AnyFunction)
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
def declare_lifecycle(
    func: TLifecycle,
    /,
    *,
    name: str | None = None,
    registry: HooksRegistry,
    structure_registry: "StructureRegistry | None" = None,
) -> TLifecycle: ...


@overload
def declare_lifecycle(
    func: None = None,
    /,
    *,
    name: str | None = None,
    registry: HooksRegistry,
    structure_registry: "StructureRegistry | None" = None,
) -> Callable[[TLifecycle], TLifecycle]: ...


def declare_lifecycle(
    func: TLifecycle | None = None,
    /,
    *,
    name: str | None = None,
    registry: HooksRegistry,
    structure_registry: "StructureRegistry | None" = None,
) -> TLifecycle | Callable[[TLifecycle], TLifecycle]:
    """Declare a lifecycle hook: an async generator that yields once.

    It runs up to its yield among the startup hooks, in the order they were declared,
    and after it once the shutdown hooks have run, the last one entered first.

    Reached through ``AppRegistry.lifecycle``; call it directly only with ``registry=``.
    """

    def decorator(function: TLifecycle) -> TLifecycle:
        if not inspect.isasyncgenfunction(function):
            raise TypeError(
                f"A lifecycle hook must be an async generator, and {function.__name__} is not: "
                "`async def` with one `yield`."
            )
        registry.register_startup(
            name or function.__name__, LifecycleHookDeclaration(function, structure_registry)
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
