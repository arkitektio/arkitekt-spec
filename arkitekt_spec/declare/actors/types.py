"""What registering a function records about it: pure data, no actors.

A function registered as an action is analysed once, at declaration: which
parameters are states, contexts, dependencies, injected clients or the task, what it
returns into state, which locks it needs. :class:`ImplementationDetails` holds that,
and :class:`RegisterConfig` holds every option the registration was given. A runtime
builds the function's actor from these; building one is not declaration.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from arkitekt_spec.actions import (
    DefinitionInput,
    PortGroupInput,
    TestTargetInput,
    TrackInput,
    ValidatorInput,
)
from arkitekt_spec.declare.actors.policy import KEEP, DisconnectPolicy
from arkitekt_spec.declare.agents.context import PreparedContextReturns, PreparedContextVariables
from arkitekt_spec.declare.coercible_types import OptimisticCoercible
from arkitekt_spec.declare.definition.define import (
    AssignWidgetMap,
    EffectsMap,
    ReturnWidgetMap,
)
from arkitekt_spec.declare.protocol.types import AnyFunction

if TYPE_CHECKING:
    from arkitekt_spec.declare.structures.registry import StructureRegistry


@dataclass
class PreparedStateVariables:
    write_state_variables: dict[str, str]
    read_only_variables: dict[str, str]
    required_state_locks: dict[str, list[str]]

    @property
    def count(self) -> int:
        """Get the amount of state variables."""
        return len(self.write_state_variables) + len(self.read_only_variables)

    @property
    def variable_keys(self) -> list[str]:
        """Get the keys of the state variables."""
        return list(self.write_state_variables.keys()) + list(
            self.read_only_variables.keys()
        )


@dataclass
class PreparedAppContextVariables:
    app_context_variables: dict[str, type[Any]]
    """The app-context class each parameter asks for, by parameter name."""

    @property
    def count(self) -> int:
        """Get the amount of state variables."""
        return len(self.app_context_variables)


@dataclass
class PreparedInjectedVariables:
    """The parameters of a function that are injected rather than ports.

    ``service_client_variables`` map a parameter to the client class it receives
    from the app the agent is bound to (``mikro: Mikro``); ``task_variables``
    receive the :class:`rekuest.task.Task` being run; ``app_context_variables``
    receive the app context the run started the agent with, keyed by the class
    the app declared.
    """

    service_client_variables: dict[str, type] = field(default_factory=dict)
    task_variables: list[str] = field(default_factory=list)
    app_context_variables: dict[str, type] = field(default_factory=dict)

    @property
    def count(self) -> int:
        """Get the amount of injected variables."""
        return (
            len(self.service_client_variables)
            + len(self.task_variables)
            + len(self.app_context_variables)
        )


@dataclass
class PreparedDependencyVariables:
    dependency_variables: dict[str, Any]


@dataclass
class PreparedStateReturns:
    state_returns: dict[int, str]

    @property
    def count(self) -> int:
        """Get the amount of state returns."""
        return len(self.state_returns)


@dataclass
class PreparedAppContextReturns:
    app_context_returns: dict[int, type[Any]]
    """The app-context class each return position publishes, by index."""

    @property
    def count(self) -> int:
        """Get the amount of app context returns."""
        return len(self.app_context_returns)


@dataclass
class ImplementationDetails:
    state_variables: PreparedStateVariables
    state_returns: PreparedStateReturns
    context_variables: PreparedContextVariables
    context_returns: PreparedContextReturns
    dependency_variables: PreparedDependencyVariables
    locks: list[str] | None = None
    tracks: list["TrackInput"] | None = None
    manipulates: list[str] | None = None
    injected_variables: PreparedInjectedVariables = field(
        default_factory=PreparedInjectedVariables
    )

    def actor_kwargs(self) -> dict[str, Any]:
        """Everything derived from the function that its actor is built with.

        Every actifier passes this whole, so a new kind of injected parameter
        reaches every actor: hand-listing the fields is how the Qt builder and
        fluss's flow actifier each silently dropped the injected variables.
        """
        return {
            "state_variables": self.state_variables,
            "state_returns": self.state_returns,
            "context_variables": self.context_variables,
            "context_returns": self.context_returns,
            "dependency_variables": self.dependency_variables,
            "injected_variables": self.injected_variables,
            "locks": self.locks,
        }


@dataclass
class RegisterConfig:
    """Bundle of every option that shapes a registered function's definition and
    implementation.

    This is the single source of truth for the registration options. The public
    ``register`` decorator builds one of these from its keyword arguments and threads
    it — as a single object — down through ``register_func`` and the actifier, instead
    of re-listing ~20 parameters at every hop.

    The fields fall into two groups:

    * **definition-shaping** — unpacked by the actifier into ``prepare_definition``:
      ``name``, ``description``, ``widgets``, ``return_widgets``, ``port_effects``,
      ``validators``, ``collections``, ``port_groups``,
      ``is_test_for``, ``stateful``, ``version``, ``key``.
    * **implementation/actor-shaping** — used by the actifier's actor build and by
      ``register_func`` when constructing the ``ImplementationInput``:
      ``optimistics``, ``locks``, ``tracks``, ``manipulates``, ``in_process``,
      ``bypass_shrink``, ``bypass_expand``, ``auto_locks``, ``concurrency``,
      ``policy``.
    """

    # definition-shaping
    name: str | None = None
    description: str | None = None
    interface: str | None = None
    widgets: AssignWidgetMap | None = None
    return_widgets: ReturnWidgetMap | None = None
    port_effects: EffectsMap | None = None
    validators: dict[str, list[ValidatorInput]] | None = None
    collections: list[str] | None = None
    port_groups: list[PortGroupInput] | None = None
    is_test_for: list[TestTargetInput] | None = None
    stateful: bool = False
    version: str | None = None
    key: str | None = None
    catalogs: list[str] | None = None
    """Names of the UI catalogs that extend the base catalog (``base@1``, always applied) for the definition's effect and validator calls."""
    # implementation / actor-shaping
    optimistics: list[OptimisticCoercible] | None = None
    locks: list[str] | None = None
    tracks: list[TrackInput] | None = None
    manipulates: list[str] | None = None
    in_process: bool = False
    bypass_shrink: bool = False
    bypass_expand: bool = False
    auto_locks: bool = True
    concurrency: Literal["parallel", "serial"] = "serial"
    policy: DisconnectPolicy = KEEP


@runtime_checkable
class Actifier(Protocol):
    """Turns a function into its definition, implementation details and actor builder.

    The builder is the runtime's (an actor needs an agent), so it is ``Any`` here: a
    declaration that is handed an actifier calls it for the definition and details,
    and keeps the actifier for the runtime to build the actor with.
    """

    def __call__(
        self,
        function: AnyFunction,
        structure_registry: "StructureRegistry",
        config: RegisterConfig | None = None,
    ) -> tuple[DefinitionInput, ImplementationDetails, Any]:
        """Inspect the function; return its definition, details and actor builder."""
        ...



@dataclass
class DeclaredImplementation:
    """What a runtime needs to run a registered action: the function and how it was registered.

    The declaration keeps this rather than an actor builder: building an actor needs an
    agent, which is the runtime's. ``actifier`` is the one the registration was given
    (a Qt app's, fluss's), or ``None`` for the runtime's default.
    """

    function: AnyFunction
    config: RegisterConfig
    actifier: Actifier | None = None
