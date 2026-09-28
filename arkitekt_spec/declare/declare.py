"""Declared protocols: what an app demands of *other* apps.

A protocol class describes a remote agent by its public methods (action
demands) and its public annotated attributes (state demands, each annotated
with a class whose annotations are the state's fields). It is declared on an
app -- ``@app.declare(app="lab")`` -- and its ports are built then and there,
against that app's structure registry. Nothing is written on the
class: the app's :class:`~rekuest.structures.registry.StructureRegistry` keeps
the :class:`DeclaredAgentProtocol` under the class, so one class can be declared
on any number of apps, each building its own ports.
"""

import inspect
from typing import (
    Any,
    ClassVar,
    Generic,
    ParamSpec,
    TypeVar,
    get_origin,
    get_type_hints,
)

from arkitekt_spec.actions import (
    ActionDependencyInput,
    AgentDependencyInput,
    DefinitionInput,
    ReturnPortInput,
    StateDefinitionInput,
    StateDependencyInput,
)
from arkitekt_spec.declare.definition.define import convert_object_to_returnport, prepare_definition
from arkitekt_spec.declare.definition.demands import (
    ActionDemandOverride,
    StateDemandOverride,
    demand,
    demand_state,
    get_action_demand_override,
    get_state_demand_override,
    unwrap_annotated,
)
from arkitekt_spec.declare.definition.dependencies import (
    build_action_dependency_input,
    build_state_dependency_input,
)
from arkitekt_spec.declare.definition.errors import DefinitionError
from arkitekt_spec.declare.definition.utils import interface_name
from arkitekt_spec.declare.protocol.types import AnyFunction
from arkitekt_spec.declare.structures.registry import StructureRegistry

P = ParamSpec("P")
R = TypeVar("R")


class DeclaredAgentAction(Generic[P, R]):
    """One public method of a declared protocol: an action demanded of the remote agent."""

    def __init__(
        self,
        func: AnyFunction,
        agent_interface: str,
        key: str,
        structure_registry: StructureRegistry,
        app: str | None = None,
    ) -> None:
        """Build the demand, its definition included.

        Args:
            func: The protocol method.
            agent_interface: The interface of the protocol it belongs to.
            key: The method's name on the protocol.
            structure_registry: The declaring app's structures, which the
                definition's ports are built against.
            app: The remote app the demand is directed at, if any.
        """
        self.func = func
        self.agent_interface = agent_interface
        self.key = key
        self.app = app
        self.override: ActionDemandOverride | None = get_action_demand_override(func)
        self.is_async = inspect.iscoroutinefunction(func)
        self.interface = func.__name__
        self.definition: DefinitionInput = prepare_definition(
            func,
            omitfirst=1,  # the protocol's `self`
            structure_registry=structure_registry,
        )

    def to_dependency_input(self) -> ActionDependencyInput:
        """The demand as the server takes it.

        By default it inherits its ``app`` from the protocol and its ``key`` from
        the method name; a :func:`demand` override on the method redirects it.
        """
        override = self.override
        return build_action_dependency_input(
            key=self.interface,
            definition=self.definition,
            app=override.app if override and override.app is not None else self.app,
            action_key=(
                override.key
                if override and override.key is not None
                else self.interface
            ),
            version=override.version if override else None,
            hash=override.hash if override else None,
            name=override.name if override else None,
            protocols=override.protocols if override else None,
            force_arg_length=override.force_arg_length if override else None,
            force_return_length=override.force_return_length if override else None,
            match_ports=override.match_ports if override else True,
            optional=override.optional if override else False,
        )


class DeclaredAgentState:
    """One annotated attribute of a declared protocol: a state demanded of the remote agent."""

    def __init__(
        self,
        stateclass: type,
        agent_interface: str,
        key: str,
        structure_registry: StructureRegistry,
        app: str | None = None,
        override: "StateDemandOverride | None" = None,
    ) -> None:
        """Build the demand, its definition included.

        Args:
            stateclass: The state's shape: a class whose annotations are its fields.
            agent_interface: The interface of the protocol it belongs to.
            key: The attribute's name on the protocol.
            structure_registry: The declaring app's structures, which the
                definition's ports are built against.
            app: The remote app the demand is directed at, if any.
            override: A :func:`demand_state` marker found on the annotation.
        """
        self.func = stateclass
        self.agent_interface = agent_interface
        self.key = key
        self.interface = key
        self.app = app
        self.override: StateDemandOverride | None = (
            override if override is not None else get_state_demand_override(stateclass)
        )
        self.definition: StateDefinitionInput = inspect_declared_state(
            stateclass, structure_registry
        )

    def to_dependency_input(self) -> StateDependencyInput:
        """The demand as the server takes it.

        By default it inherits its ``app`` from the protocol and its ``key`` from
        the attribute name; a :func:`demand_state` marker redirects it.
        """
        override = self.override
        return build_state_dependency_input(
            key=self.key,
            definition=self.definition,
            state_key=(
                override.key
                if override and override.key is not None
                else self.interface
            ),
            app=override.app if override and override.app is not None else self.app,
            hash=override.hash if override else None,
            protocols=override.protocols if override else None,
            match_ports=override.match_ports if override else True,
            optional=override.optional if override else False,
        )


Agent = TypeVar("Agent")


T = TypeVar("T")


def inspect_declared_state(
    stateclass: type[Any], structure_registry: StructureRegistry
) -> StateDefinitionInput:
    type_hints = get_type_hints(stateclass, include_extras=True)
    ports: list[ReturnPortInput] = []

    for field_name, field_type in type_hints.items():
        default = getattr(stateclass, field_name, None)
        port = convert_object_to_returnport(
            cls=field_type,
            key=field_name,
            default=default,
            registry=structure_registry,
        )
        ports.append(port)

    return StateDefinitionInput(
        ports=tuple(ports),
        name=stateclass.__name__,
    )


class DeclaredAgentProtocol(Generic[Agent]):
    """A protocol class as one app declared it: its demands, ports built.

    Made by :meth:`AppRegistry.declare <rekuest.app.AppRegistry.declare>` and kept
    by the app's structure registry under the class. An action parameter
    annotated with the class is handed a proxy that calls the remote agent; a
    blok names it under a key in ``dependencies``.
    """

    def __init__(
        self,
        func: type[Agent],
        structure_registry: StructureRegistry,
        app: str | None = None,
        min: int | None = None,
        max: int | None = None,
        version: str | None = None,
        auto_resolvable: bool = False,
        description: str | None = None,
        allow_inactive: bool = True,
    ) -> None:
        """Inspect the class and build every demand against ``structure_registry``.

        Args:
            func: The protocol class.
            structure_registry: The declaring app's structures.
            app: The remote app the protocol is directed at, if any.
            min: Minimum viable number of matching agents.
            max: Maximum viable number of matching agents.
            version: The protocol's version.
            auto_resolvable: Whether any matching agent may be assigned
                automatically.
            description: What the protocol is for. Defaults to the class docstring.
            allow_inactive: Whether an inactive agent may satisfy it.
        """
        self.func = func
        self.app = app
        self.description = description or func.__doc__
        self.allow_inactive = allow_inactive
        self.interface = interface_name(func)
        self.actions: dict[str, DeclaredAgentAction[Any, Any]] = {}
        self.states: dict[str, DeclaredAgentState] = {}
        self.auto_resolvable = auto_resolvable
        self.min = min
        self.max = max
        self.version: str | None = version

        type_hints = get_type_hints(func, include_extras=True)

        # Every public annotated attribute is a state demand: nothing marks a
        # state shape, so the annotation has to be a class of its own.
        for dependency_key, annotation in type_hints.items():
            if dependency_key.startswith("_") or get_origin(annotation) is ClassVar:
                continue

            state_cls = unwrap_annotated(annotation)
            if not (inspect.isclass(state_cls) and state_cls.__module__ != "builtins"):
                raise DefinitionError(
                    f"{func.__qualname__}.{dependency_key} is annotated "
                    f"{annotation!r}, which is not a state shape. A protocol's public "
                    "annotated attributes are the states it demands of the remote "
                    "agent: annotate one with a class whose annotations are the "
                    "state's fields, prefix it with `_` to keep it private, or make "
                    "it a method."
                )
            self.states[dependency_key] = DeclaredAgentState(
                state_cls,
                self.interface,
                key=dependency_key,
                structure_registry=structure_registry,
                app=self.app,
                override=get_state_demand_override(annotation),
            )

        for dependeny_key, method in inspect.getmembers(func):
            if not dependeny_key.startswith("_") and callable(method):
                action: DeclaredAgentAction[Any, Any] = DeclaredAgentAction(
                    method,
                    self.interface,
                    key=dependeny_key,
                    structure_registry=structure_registry,
                    app=self.app,
                )
                self.actions[dependeny_key] = action

    def to_dependency_input(self, key: str) -> AgentDependencyInput:
        """This protocol as a dependency under ``key``, as the server takes it."""
        return AgentDependencyInput(
            key=key,
            app=self.app,
            description=self.description or self.func.__doc__,
            actionDependencies=tuple(
                action.to_dependency_input() for action in self.actions.values()
            ),
            stateDependencies=tuple(
                state.to_dependency_input() for state in self.states.values()
            ),
            autoResolvable=self.auto_resolvable,
            optional=False,
            minViableInstances=self.min,
            maxViableInstances=self.max,
            version=self.version,
        )


__all__ = [
    "ActionDemandOverride",
    "DeclaredAgentAction",
    "DeclaredAgentProtocol",
    "DeclaredAgentState",
    "StateDemandOverride",
    "demand",
    "demand_state",
]
