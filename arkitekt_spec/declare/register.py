"""Register a function or actor with the definition registry."""

import hashlib
import inspect
import warnings
from collections.abc import Callable
from typing import (
    TYPE_CHECKING,
    Generic,
    Literal,
    ParamSpec,
    TypeVar,
    cast,
    overload,
)

if TYPE_CHECKING:
    from arkitekt_spec.declare.app import AppRegistry
import functools
import logging

from arkitekt_spec.actions import (
    ActionDependencyInput,
    AgentDependencyInput,
    AssignWidgetInput,
    DefinitionInput,
    EffectInput,
    Effects,
    Execution,
    ImplementationInput,
    OptimisticInput,
    PortGroupInput,
    TestTargetInput,
    TrackInput,
    ValidatorInput,
    definition_hash,
)
from arkitekt_spec.declare.actors.actify import (
    derive_implementation_details,
    prepare_definition_from_config,
)
from arkitekt_spec.declare.actors.policy import KEEP, DisconnectPolicy
from arkitekt_spec.declare.actors.types import (
    Actifier,
    DeclaredImplementation,
    RegisterConfig,
)
from arkitekt_spec.declare.coercible_types import (
    OptimisticCoercible,
)
from arkitekt_spec.declare.definition.checks import check_implementation
from arkitekt_spec.declare.definition.errors import DefinitionError
from arkitekt_spec.declare.definition.define import (
    dependency_to_dependency_input,
)
from arkitekt_spec.declare.definition.dependencies import build_action_dependency_input
from arkitekt_spec.declare.definition.utils import interface_name
from arkitekt_spec.declare.protocol.types import AnyFunction
from arkitekt_spec.declare.structures.registry import StructureRegistry

logger = logging.getLogger(__name__)


P = ParamSpec("P")
R = TypeVar("R")


class WrappedFunction(Generic[P, R]):
    """A registered function: still the plain function, plus what it registered as.

    Calling it remotely goes through a client, which knows the app it runs in:
    ``rekuest.call(fn, ...)`` finds its implementation there.
    """

    def __init__(
        self, func: Callable[P, R], interface: str, definition: DefinitionInput
    ) -> None:
        """Initialize the wrapped function."""
        functools.update_wrapper(self, func)  # name, doc, signature: still the function
        self.func = func
        self.interface = interface
        self.definition = definition
        self.hash = definition_hash(definition)

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R:
        """Call the actor's implementation."""
        return self.func(*args, **kwargs)

    def __reduce__(self) -> str:
        """Pickle by name, like the function it replaced at module level.

        The decorated name now holds this wrapper, so pickling the inner function
        by reference would find the wrapper there and refuse.

        Returns:
            The qualified name pickle looks the wrapper up under.
        """
        qualname: str = self.func.__qualname__
        return qualname

    def to_dependency_input(self) -> ActionDependencyInput:
        """Convert the wrapped function to a DependencyInput."""
        return build_action_dependency_input(
            key=self.interface,
            definition=self.definition,
            optional=False,
        )


def _warn_if_cancel_cannot_stop(function: AnyFunction, config: RegisterConfig) -> None:
    """A synchronous action runs in a worker thread, which cannot be force-killed.

    Warned here, at registration, rather than raised: a threaded action that *does*
    poll for cancellation is perfectly valid, and nothing can tell from outside.
    """
    runs_threaded = not (
        inspect.iscoroutinefunction(function) or inspect.isasyncgenfunction(function)
    ) and not config.in_process
    if config.policy.cancels_on_disconnect and runs_threaded:
        warnings.warn(
            f"{getattr(function, '__name__', function)!r} asks to be cancelled on "
            "disconnect but runs in a worker thread, which cannot be force-killed. "
            "It will only stop if its body calls koil.check_cancelled().",
            UserWarning,
            stacklevel=4,
        )


def code_hash_of(function_or_actor: AnyFunction) -> str | None:
    """A hash of what the implementation runs: its source, else its bytecode.

    A workflow is only resumed by an implementation with the same hash, so an edit
    between a crash and the resume can't replay a journal onto different code.
    """
    try:
        code = inspect.getsource(function_or_actor).encode()
    except (OSError, TypeError):
        raw = getattr(getattr(function_or_actor, "__code__", None), "co_code", None)
        if raw is None:
            return None
        code = raw
    return hashlib.sha256(code).hexdigest()


def register_func(
    function_or_actor: AnyFunction,
    structure_registry: StructureRegistry,
    implementation_registry: "AppRegistry",
    config: RegisterConfig | None = None,
    *,
    actifier: Actifier | None = None,
) -> DefinitionInput:
    """Register a function or actor with the provided app registry.

    The function is analysed into its definition and implementation, and recorded
    with how it was registered, at ``config.interface``, else ``config.key``, else an
    interface name inferred from the function name. Its actor is the runtime's to
    build (see :class:`~arkitekt_spec.declare.actors.types.DeclaredImplementation`).

    Args:
        function_or_actor (AnyFunction): A function or actor to be registered.
        structure_registry (StructureRegistry): The registry used for structuring inputs.
        implementation_registry (AppRegistry): The registry where implementations are stored.
        config (Optional[RegisterConfig], optional): Bundled registration options.
            Defaults to an empty ``RegisterConfig``.
        actifier (Actifier, optional): A runtime's actifier, when the action must be
            run by a particular kind of actor (Qt, fluss). It is asked for the
            definition and kept for the runtime; ``None`` leaves both to the defaults.

    Returns:
        DefinitionInput: The registered definition.
    """
    config = config or RegisterConfig()
    interface = config.interface or config.key or interface_name(function_or_actor)

    _warn_if_cancel_cannot_stop(function_or_actor, config)

    if actifier is not None:
        definition, implementation_details, _ = actifier(
            function_or_actor,
            structure_registry,
            config,
        )
    else:
        implementation_details = derive_implementation_details(
            function_or_actor, config, structure_registry
        )
        definition = prepare_definition_from_config(
            function_or_actor, structure_registry, config, implementation_details
        )

    dependencies: list[AgentDependencyInput] = []
    for (
        key,
        dependency,
    ) in implementation_details.dependency_variables.dependency_variables.items():
        dependencies.append(
            dependency_to_dependency_input(key, dependency, structure_registry)
        )

    if config.execution == Execution.PLAIN:
        calling = [d.key for d in dependencies if d.action_dependencies]
        if calling:
            raise DefinitionError(
                f"{interface} calls other actions (through {', '.join(calling)}), "
                "which only a workflow may do: register it with @app.workflow. "
                "A protocol with only state attributes is fine on a plain action."
            )

    optimistics: list[OptimisticInput] = [
        optimistic
        if isinstance(optimistic, OptimisticInput)
        else optimistic.to_optimistic_input()
        for optimistic in (config.optimistics or [])
    ]

    # The spec's models carry the structural rules; what needs rekuest (search
    # queries, state references) is checked here, at the decorator.
    implementation_registry.register_at_interface(
        interface,
        check_implementation(ImplementationInput(
            interface=interface,
            definition=definition,
            locks=tuple(implementation_details.locks or []),
            optimistics=tuple(optimistics),
            dependencies=tuple(dependencies),
            tracks=tuple(implementation_details.tracks or []),
            needs_token=True,  # TODO: Make this configurable in the future, but for now, we want to ensure that all actors require tokens for security reasons.
            manipulates=tuple(implementation_details.manipulates or []),
            effects=config.effects or implementation_registry.default_effects,
            execution=config.execution,
            code_hash=code_hash_of(function_or_actor),
        )),
        DeclaredImplementation(function_or_actor, config, actifier),
    )

    return definition




@overload
def declare_implementation(
    func: Callable[P, R],
    /,
    *,
    implementation_registry: "AppRegistry",
    key: str | None = None,
    name: str | None = None,
    description: str | None = None,
    actifier: Actifier | None = None,
    interface: str | None = None,
    stateful: bool = False,
    widgets: dict[str, AssignWidgetInput] | None = None,
    collections: list[str] | None = None,
    port_groups: list[PortGroupInput] | None = None,
    port_effects: dict[str, list[EffectInput]] | None = None,
    is_test_for: list[TestTargetInput] | None = None,
    validators: dict[str, list[ValidatorInput]] | None = None,
    structure_registry: StructureRegistry | None = None,
    optimistics: list[OptimisticCoercible] | None = None,
    in_process: bool = False,
    tracks: list[TrackInput] | None = None,
    locks: list[str] | None = None,
    concurrency: Literal["parallel", "serial"] = "serial",
    policy: DisconnectPolicy = KEEP,
    version: str | None = None,
    catalogs: list[str] | None = None,
    effects: Effects | None = None,
    execution: Execution = Execution.PLAIN,
) -> WrappedFunction[P, R]:
    """Register a function directly: ``declare_implementation(fn, implementation_registry=...)``."""


@overload
def declare_implementation(
    func: None = None,
    /,
    *,
    implementation_registry: "AppRegistry",
    key: str | None = None,
    name: str | None = None,
    description: str | None = None,
    actifier: Actifier | None = None,
    interface: str | None = None,
    stateful: bool = False,
    widgets: dict[str, AssignWidgetInput] | None = None,
    collections: list[str] | None = None,
    port_groups: list[PortGroupInput] | None = None,
    port_effects: dict[str, list[EffectInput]] | None = None,
    is_test_for: list[TestTargetInput] | None = None,
    validators: dict[str, list[ValidatorInput]] | None = None,
    structure_registry: StructureRegistry | None = None,
    optimistics: list[OptimisticCoercible] | None = None,
    in_process: bool = False,
    tracks: list[TrackInput] | None = None,
    locks: list[str] | None = None,
    concurrency: Literal["parallel", "serial"] = "serial",
    policy: DisconnectPolicy = KEEP,
    version: str | None = None,
    catalogs: list[str] | None = None,
    effects: Effects | None = None,
    execution: Execution = Execution.PLAIN,
) -> Callable[[Callable[P, R]], WrappedFunction[P, R]]:
    """Build a configured decorator: ``declare_implementation(implementation_registry=..., name=...)``."""


def declare_implementation(
    func: Callable[P, R] | None = None,
    /,
    *,
    implementation_registry: "AppRegistry",
    key: str | None = None,
    name: str | None = None,
    actifier: Actifier | None = None,
    interface: str | None = None,
    stateful: bool = False,
    description: str | None = None,
    widgets: dict[str, AssignWidgetInput] | None = None,
    collections: list[str] | None = None,
    port_groups: list[PortGroupInput] | None = None,
    port_effects: dict[str, list[EffectInput]] | None = None,
    is_test_for: list[TestTargetInput] | None = None,
    optimistics: list[OptimisticCoercible] | None = None,
    validators: dict[str, list[ValidatorInput]] | None = None,
    structure_registry: StructureRegistry | None = None,
    tracks: list[TrackInput] | None = None,
    in_process: bool = False,
    locks: list[str] | None = None,
    concurrency: Literal["parallel", "serial"] = "serial",
    policy: DisconnectPolicy = KEEP,
    version: str | None = None,
    catalogs: list[str] | None = None,
    effects: Effects | None = None,
    execution: Execution = Execution.PLAIN,
) -> WrappedFunction[P, R] | Callable[[Callable[P, R]], WrappedFunction[P, R]]:
    """Register a function or actor with an app registry.

    The implementation behind ``@app.register`` (:meth:`AppRegistry.register`) and
    arkitekt's ``@app.action``. Registration goes through an app, which owns the registry
    this writes into -- there is no process-wide one, on purpose, because it made what an
    app ran depend on what the process had imported. Not part of the package's public
    surface (see ``rekuest.__all__``); call it through the registry that owns it.

    All keyword arguments are bundled into a single :class:`RegisterConfig` that is
    threaded through ``register_func`` and the actifier.

    Directly, from the registry that owns it::

        app.register(my_function)

    Or configured, returning the decorator it applies::

        app.register(interface="custom_interface", widgets={...})(my_function)

    Args:
        func: The function or actor, when registering one directly. Omitted to get a
            configured decorator back.
        implementation_registry: The app registry the implementation is recorded in.
        key (Optional[str]): The action's key: what the server identifies it by,
            together with ``version``. Defaults to the function name, so functions
            made by one factory need distinct keys. Also the default interface.
        name (Optional[str]): Display name. Defaults to the function name.
        description (Optional[str]): Description. Defaults to the docstring.
        actifier (Actifier | None): A runtime's actifier, for actions a particular
            kind of actor must run; ``None`` leaves it to the runtime's default.
        interface (Optional[str]): Interface name. Defaults to ``key``, else is
            inferred from the function name.
        stateful (bool): Mark the definition stateful (auto-set when the
            function uses state variables).
        widgets (Optional[Dict[str, AssignWidgetInput]]): Widgets per argument.
        collections (Optional[List[str]]): Organizational groupings.
        port_groups (Optional[List[PortGroupInput]]): Port group assignments.
        port_effects (Optional[Dict[str, List[EffectInput]]]): UI effects per port.
        is_test_for (Optional[List[TestTargetInput]]): Actions this function is a
            test for, each identified by hash or by (app, key, version).
        validators (Optional[Dict[str, List[ValidatorInput]]]): Input validation
            rules per argument.
        structure_registry (Optional[StructureRegistry]): Structures used to build
            the ports. Defaults to ``implementation_registry.structure_registry``.
        optimistics (Optional[List[OptimisticCoercible]]): Optimistic outputs.
        in_process (bool): Run the actor in the event loop instead of a thread.
        tracks (Optional[List[TrackInput]]): Tracks the implementation follows.
        locks (Optional[List[str]]): Resource locks held during assignment
            (auto-inferred from state/context locks when omitted).
        concurrency (Literal["parallel", "serial"]): Whether assignments to the
            actor may run concurrently ("parallel") or one at a time
            ("serial", the default).
        version (Optional[str]): Version of the definition.
        effects (Effects | None): What running it again would do to the world.
            Informational. None takes the app's default.
        execution (Execution): WORKFLOW to call other actions and be resumed
            after a crash; PLAIN otherwise.

    Returns:
        The wrapped function, or a decorator producing it.
    """
    if structure_registry is None:
        structure_registry = implementation_registry.structure_registry

    config = RegisterConfig(
        key=key,
        name=name,
        description=description,
        interface=interface,
        widgets=widgets,
        port_effects=port_effects,
        validators=validators,
        collections=collections,
        port_groups=port_groups,
        is_test_for=is_test_for,
        stateful=stateful,
        version=version,
        catalogs=catalogs,
        optimistics=optimistics,
        locks=locks,
        concurrency=concurrency,
        policy=policy,
        tracks=tracks,
        in_process=in_process,
        effects=effects,
        execution=execution,
    )

    def offer(function_or_actor: Callable[P, R]) -> WrappedFunction[P, R]:
        any_function = cast(AnyFunction, function_or_actor)
        iface = config.interface or config.key or interface_name(any_function)

        definition = register_func(
            any_function,
            structure_registry,
            implementation_registry,
            config,
            actifier=actifier,
        )

        return WrappedFunction(function_or_actor, iface, definition)

    return offer(func) if func is not None else offer
