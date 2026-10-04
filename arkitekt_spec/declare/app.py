"""Global App Registry for Rekuest.

This module provides a single unified registry that holds every piece of agent
registration data — implementations, states and bloks — together with the hooks
and structure registries.
"""

import warnings
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Literal,
    ParamSpec,
    TypeVar,
    dataclass_transform,
    overload,
)

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, SkipValidation

from arkitekt_spec.actions import (
    AgentDependencyInput,
    AssignWidgetInput,
    BlokImplementationInput,
    ComponentNodeInput,
    EffectInput,
    Effects,
    Execution,
    ImplementAgentInput,
    ImplementationInput,
    LockDefinitionInput,
    LockImplementationInput,
    PortGroupInput,
    PortKind,
    ReturnWidgetInput,
    StateImplementationInput,
    TestTargetInput,
    TrackInput,
    ValidatorInput,
)
from arkitekt_spec.declare.actors.policy import KEEP, DisconnectPolicy
from arkitekt_spec.declare.actors.types import Actifier, DeclaredImplementation
from arkitekt_spec.declare.agents.hooks.registry import HooksRegistry
from arkitekt_spec.declare.blok.parser import bsx as parse_bsx
from arkitekt_spec.declare.blok.registry import build_declared_bloks
from arkitekt_spec.declare.blok.validate import RESERVED_ROOTS, validate_blok_catalog
from arkitekt_spec.declare.catalogs import (
    Catalog,
    CatalogView,
    CatalogWarning,
    ComponentSpec,
    OperationSpec,
    base_only_view,
    check_extension_does_not_shadow_base,
    resolve_catalogs,
)
from arkitekt_spec.declare.coercible_types import OptimisticCoercible
from arkitekt_spec.declare.errors import AppContextError, RegistryFrozenError
from arkitekt_spec.declare.protocol.types import AnyState
from arkitekt_spec.declare.provider import Provider, declare_provider
from arkitekt_spec.declare.service import Service, declare_service
from arkitekt_spec.declare.structures.errors import StructureRegistryError
from arkitekt_spec.declare.structures.model import model_field
from arkitekt_spec.declare.structures.registry import StructureRegistry
from arkitekt_spec.declare.structures.types import (
    ExpanderT,
    ManyExpander,
    Shrinker,
    StateDeclaration,
)

T = TypeVar("T")
P = ParamSpec("P")
R = TypeVar("R")

if TYPE_CHECKING:
    from arkitekt_spec.declare.register import WrappedFunction


class BlokDeclaration(BaseModel):
    """Everything :meth:`AppRegistry.register_blok` records for one blok."""

    component: ComponentNodeInput
    description: str | None = None
    demo_state: dict[str, Any] | None = None
    local_state: dict[str, Any] | None = None
    dependencies: list[AgentDependencyInput] | None = None
    catalog: str | None = None


def _owner_among(
    services: "Mapping[str, Service[Any]]",
) -> "Callable[[Any], str | None]":
    """Name, for a structure, the one of ``services`` whose client expands it.

    The service whose function returns what the expander asks for owns the
    structure. With a single service every structure is its; with none, no
    structure is owned.
    """
    names = list(services)

    def owner(structure: Any) -> str | None:
        if len(names) == 1:
            return names[0]
        for name in names:
            returned = services[name].returns
            if any(
                issubclass(returned, wanted) for wanted in structure.injects.values()
            ):
                return name
        return None

    return owner


class AppRegistry(BaseModel):
    """The single registry that consolidates all agent registration data.

    The AppRegistry stores function implementations, observable states and bloks,
    and exposes both the storage methods (used by the ``@app.register``/``@app.state``
    decorators and the agent) and the decorator surface itself.

    Example:
        ```python
        app = AppRegistry()

        @app.register
        def my_function(x: int) -> int:
            return x * 2

        @app.state
        class MyState:
            value: int
        ```
    """

    # --- implementations (formerly DefinitionRegistry) ---
    implementations: dict[str, ImplementationInput] = Field(
        default_factory=dict, exclude=True
    )
    declared_implementations: Annotated[
        dict[str, DeclaredImplementation], SkipValidation
    ] = Field(default_factory=dict, exclude=True)
    """What a runtime builds each implementation's actor from, by interface."""
    default_effects: Effects = Effects.UNKNOWN
    """What running an action again would do, for actions that make no claim of their own."""

    # --- states (formerly StateRegistry) ---
    states: dict[str, StateImplementationInput] = Field(
        default_factory=dict, exclude=True
    )
    state_registry_schemas: dict[str, StructureRegistry] = Field(
        default_factory=dict, exclude=True
    )
    state_interface_classes: dict[str, AnyState] = Field(
        default_factory=dict, exclude=True
    )
    state_classes_interfaces: dict[type[AnyState], str] = Field(
        default_factory=dict, exclude=True
    )

    # --- bloks (formerly BlokRegistry) ---
    registered_bloks: dict[str, "BlokDeclaration"] = Field(default_factory=dict)
    declared_catalogs: dict[str, Catalog] = Field(default_factory=dict)

    # --- other registries kept as composed fields ---
    hooks_registry: HooksRegistry = Field(default_factory=HooksRegistry)
    structure_registry: StructureRegistry = Field(default_factory=StructureRegistry)

    # --- services ---
    services: dict[str, Service[Any]] = Field(default_factory=dict, exclude=True)
    """The services this app uses, by name: what a run builds its clients from.

    Declared here with :meth:`service`, or taken in from the registry a client
    package ships (:meth:`register_service`, :meth:`merge`). There is no
    process-wide catalog: a service reaches exactly the apps that take it in.
    """
    providers: dict[str, Provider[Any]] = Field(default_factory=dict, exclude=True)
    """How this app's offerings are served, by name: what a run builds its agent from.

    Declared with :meth:`provider` or taken in with :meth:`register_provider`.
    An app is served by one agent, so there is at most one.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # ------------------------------------------------------------------ #
    # Services                                                           #
    # ------------------------------------------------------------------ #

    def service(
        self,
        *,
        schema: str | Path | None = None,
        turms: str | Path | None = None,
    ) -> Callable[[Callable[..., T]], Service[T]]:
        """Declare a service here: ``@registry.service()`` on its builder.

        The function's name is the service's, its return annotation is the
        client, and its parameters are what a run hands it (see
        :mod:`rekuest.service`). From then on a parameter annotated with what it
        returns -- on an action, a hook or a structure's expander declared here --
        is handed the built client instead of becoming a port.

        Args:
            schema: Path to the service's GraphQL schema, for code generation.
            turms: Path to the turms project that generates its client.

        Returns:
            A decorator returning the :class:`~rekuest.service.Service`.

        Raises:
            ServiceDefinitionError: If the signature does not say what a run needs.
            ValueError: If a service of that name is declared here already.
        """

        def declare(function: Callable[..., T]) -> Service[T]:
            declared = declare_service(self, function, schema=schema, turms=turms)
            self._take_service(declared)
            return declared

        return declare

    def declare(
        self,
        app: str | None = None,
        *,
        auto_resolvable: bool = False,
        min: int | None = None,
        max: int | None = None,
        version: str | None = None,
    ) -> Callable[[type[T]], type[T]]:
        """Declare a protocol class: what this app demands of another app.

        ``@registry.declare(app="lab") class CameraProtocol: ...``. Every demand's
        ports are built now, against this registry's structures, and the class
        is returned unchanged -- see
        :meth:`StructureRegistry.declare <rekuest.structures.registry.StructureRegistry.declare>`.
        An action parameter annotated with the class is then handed a proxy that
        calls the remote agent, and a blok names it under a key in its
        ``dependencies``.

        Args:
            app: The remote app the protocol is directed at, if any.
            auto_resolvable: Whether any matching agent may be assigned
                automatically.
            min: Minimum viable number of matching agents.
            max: Maximum viable number of matching agents.
            version: The protocol's version.

        Returns:
            A class decorator returning the class.
        """
        declare = self.structure_registry.declare(
            app, auto_resolvable=auto_resolvable, min=min, max=max, version=version
        )

        def decorate(cls: type[T]) -> type[T]:
            self._refuse_if_frozen(f"the protocol {cls.__qualname__}")
            return declare(cls)

        return decorate

    def provider(self) -> Callable[[Callable[..., T]], Provider[T]]:
        """Declare a provider here: ``@registry.provider()`` on its builder.

        The function's name is the provider's, its return annotation is the
        agent's class, and its parameters are what a run hands it: the same
        injections as a service, plus any client a service declared here
        returns (see :mod:`rekuest.provider`). Declare the service first.

        Returns:
            A decorator returning the :class:`~rekuest.provider.Provider`.

        Raises:
            ProviderDefinitionError: If the signature does not say what a run needs.
            ValueError: If a different provider is declared here already.
        """

        def declare(function: Callable[..., T]) -> Provider[T]:
            declared = declare_provider(self, function)
            self._take_provider(declared)
            return declared

        return declare

    def _take_provider(self, provider: Provider[Any]) -> None:
        """Record ``provider``. The same one twice does nothing; a second one is refused."""
        self._refuse_if_frozen(f"the provider '{provider.name}'")
        existing = next(iter(self.providers.values()), None)
        if existing is not None:
            if existing is provider:
                return
            raise ValueError(
                f"An app is served by one agent, and '{existing.name}' and "
                f"'{provider.name}' both declare one."
            )
        self.providers[provider.name] = provider

    def register_provider(self, provider: Provider[Any]) -> None:
        """Use a provider declared elsewhere: ``App(providers=[rekuest_provider])``.

        Takes in the registry the provider was declared on -- the provider, the
        service beside it and the structures its package brings.

        Raises:
            RegistryFrozenError: If this registry is a running snapshot.
            ValueError: If a different provider is here already, or the
                registries clash.
        """
        if provider.registry is self:
            return
        self.merge(provider.registry)

    @property
    def provider_declaration(self) -> Provider[Any] | None:
        """The one provider this app declared, if any."""
        return next(iter(self.providers.values()), None)

    def _take_service(self, service: Service[Any]) -> None:
        """Record ``service`` and what it returns.

        The same service twice does nothing. A different service under a name
        already taken is refused: a name is what a run builds a client under, and
        what a declared structure is stamped with.
        """
        self._refuse_if_frozen(f"the service '{service.name}'")
        name = service.name
        if name in self.services:
            if self.services[name] is service:
                return
            raise ValueError(
                f"Two services are both named '{name}'. A name is what a client is "
                "built under, so an app can use only one of them."
            )
        self.services[name] = service
        self.structure_registry.clients[name] = service.returns

    def register_service(self, service: Service[Any]) -> None:
        """Use a service declared elsewhere: ``App(services=[mikro_service])``.

        Takes in the registry the service was declared on -- the service itself,
        and the structures and actions its package brings.

        Args:
            service: The service to use.

        Raises:
            RegistryFrozenError: If this registry is a running snapshot.
            ValueError: If another service of that name is registered, or its
                registry clashes with what is here already.
        """
        if service.registry is self:
            return
        self.merge(service.registry)

    def required_services(self) -> dict[str, set[str]]:
        """The services the registered implementations depend on, by structure ports.

        Maps each service name to the interfaces that use one of its structures,
        so a caller can say *which* function needs a service an app lacks. Only
        structures a service registered count: those name a service an app can
        actually be built with. A hand-registered structure's service is guessed
        from its identifier, and ``@myapp/image`` names no service at all.
        """
        needed: dict[str, set[str]] = {}

        def visit(interface: str, ports: Any) -> None:
            for port in ports or ():
                if port.kind == PortKind.STRUCTURE and port.identifier:
                    try:
                        structure = self.structure_registry.get_fullfilled_structure(
                            port.identifier
                        )
                    except (KeyError, StructureRegistryError):
                        # Unknown here, or known only as another service's. Either
                        # way the port was built, so nothing is owed to a warning.
                        structure = None
                    # Only a structure a *service* registered names a service that an
                    # app could have. The service of a hand-registered one is guessed
                    # from its identifier, and `@myapp/image` names no service at all.
                    if (
                        structure is not None
                        and structure.declared
                        and structure.service
                    ):
                        needed.setdefault(structure.service, set()).add(interface)
                visit(interface, getattr(port, "children", None))

        for interface, implementation in self.implementations.items():
            visit(interface, implementation.definition.args)
            visit(interface, implementation.definition.returns)
        return needed

    def is_empty(self) -> bool:
        """Whether nothing has been registered here: no implementation, state, hook or blok."""
        hooks = self.hooks_registry
        return not (
            self.implementations
            or self.states
            or self.registered_bloks
            or hooks.startup_hooks
            or hooks.shutdown_hooks
            or hooks.background_worker
        )

    # ------------------------------------------------------------------ #
    # Implementation storage                                             #
    # ------------------------------------------------------------------ #
    _frozen: bool = PrivateAttr(default=False)

    def freeze(self) -> None:
        """Refuse further registration, here and in the hooks registry.

        Called on a :meth:`snapshot`: what a run offers is fixed at the moment it
        connects, because that is what it told the server. The declaration itself
        is never frozen.
        """
        self._frozen = True
        self.hooks_registry.freeze()

    def _refuse_if_frozen(self, what: str) -> None:
        if self._frozen:
            raise RegistryFrozenError(
                f"Cannot register {what} on a running snapshot. Register on the "
                "app's declaration instead; a run serves what was declared when it "
                "started."
            )

    def __setattr__(self, name: str, value: object) -> None:
        """Refuse replacing a registry field wholesale once frozen.

        The `register_*` guards only cover writes *into* the dicts; without this,
        `registry.implementations = {}` would walk straight past the freeze. Private
        names pass through, or `freeze()` could not set `_frozen` itself.
        """
        if not name.startswith("_"):
            self._refuse_if_frozen(f"'{name}'")
        super().__setattr__(name, value)

    def register_at_interface(
        self,
        interface: str,
        implementation: ImplementationInput,
        declared: DeclaredImplementation,
    ) -> None:
        """Register a function or generator at the given interface."""
        self._refuse_if_frozen(f"'{interface}'")
        self.implementations[interface] = implementation
        self.declared_implementations[interface] = declared

    def get_implementations(self) -> list[ImplementationInput]:
        """Get all implementations in the registry."""
        return list(self.implementations.values())

    def get_declared_implementation(self, interface: str) -> DeclaredImplementation:
        """What the runtime builds the actor at ``interface`` from."""
        return self.declared_implementations[interface]

    def get_locks(self) -> list[LockImplementationInput]:
        """Get all lock implementations referenced by the implementations."""
        lock_implementations: dict[str, LockImplementationInput] = {}
        for schema in self.implementations.values():
            if schema.locks is not None:
                for lock in schema.locks:
                    if lock not in lock_implementations:
                        lock_implementations[lock] = LockImplementationInput(
                            key=lock,
                            definition=LockDefinitionInput(
                                key=lock,
                                description=f"Lock definition for {lock}",
                            ),
                        )
        return list(lock_implementations.values())

    # ------------------------------------------------------------------ #
    # State storage                                                      #
    # ------------------------------------------------------------------ #
    def register_state(
        self,
        cls: type[AnyState],
        state: StateImplementationInput,
        registry: StructureRegistry,
        *,
        required_locks: Sequence[str] | None = None,
    ) -> None:
        """Register a state schema at its interface, with the rules for changing it.

        The class is left as it is; what this app knows about it is recorded
        here and on the structure registry (:attr:`StructureRegistry.states`),
        which is what tells an action parameter apart as this state.

        Args:
            cls: The state class.
            state: Its schema, built against ``registry``.
            registry: The structures its ports were built against.
            required_locks: Locks an action must hold to change it.
        """
        self._refuse_if_frozen(f"the state '{state.interface}'")
        self.states[state.interface] = state
        self.state_registry_schemas[state.interface] = registry
        self.state_classes_interfaces[cls] = state.interface
        self.state_interface_classes[state.interface] = cls
        declaration = StateDeclaration(
            interface=state.interface,
            definition=state.definition,
            required_locks=tuple(required_locks or ()),
        )
        # On this app's structures, and on the ones the schema was built against
        # when those are separate: a definition built against the latter must
        # still tell the parameter apart as this state.
        self.structure_registry.states[cls] = declaration
        registry.states[cls] = declaration

    def get_registry_for_interface(self, interface: str) -> StructureRegistry:
        """Get the structure registry for a state interface."""
        assert interface in self.state_registry_schemas, "No definition for interface"
        return self.state_registry_schemas[interface]

    def get_interface_for_class(self, cls: type[AnyState]) -> str:
        """Get the interface for a state class."""
        assert cls in self.state_classes_interfaces, "No definition for class"
        return self.state_classes_interfaces[cls]

    # ------------------------------------------------------------------ #
    # Blok storage                                                       #
    # ------------------------------------------------------------------ #
    def register_blok(
        self,
        name: str,
        component: str | ComponentNodeInput | None = None,
        description: str | None = None,
        demo_state: dict[str, Any] | None = None,
        dependencies: "Mapping[str, type[Any]] | Sequence[AgentDependencyInput] | None" = None,
        catalog: str | None = None,
        local_state: dict[str, Any] | None = None,
    ) -> None:
        """Register a blok component tree in the registry.

        Dependencies referenced by the tree are inferred from this agent's own
        actions and states. Pass ``dependencies`` for anything another app
        provides: the declared protocol classes by the key the tree uses
        (``{"camera": CameraProtocol}``), since a remote action cannot be
        resolved against the local registry.

        The tree is validated now. References resolve against the dependencies;
        components, props and util operations are checked against ``catalog`` when
        this app has declared it with :meth:`declare_ui_catalog`, and findings that do
        not block registration are raised as :class:`~rekuest.catalogs.CatalogWarning`.

        Args:
            name: The blok's name.
            component: The tree, as a BSX string or already parsed.
            description: What the blok shows.
            demo_state: State to render the blok with when there is none.
            dependencies: Other apps' actions and states the tree uses.
            catalog: The UI catalog the blok renders against.
            local_state: The blok's own UI state and its initial values, e.g.
                ``{"form": {"iterations": 15}}``. Its keys are path roots the tree
                may bind controls to (``bind="form.iterations"``) and read back
                in a call (``@self.run(iterations=form.iterations)``). It never
                reaches the agent's state.

        Raises:
            ValueError: If the name or component is missing, a reference does not
                resolve, a declared catalog rejects the tree, or a ``local_state``
                key is reserved or names a dependency.
        """
        self._refuse_if_frozen(f"the blok {name!r}")
        if not name:
            raise ValueError("A blok name is required")
        if component is None:
            raise ValueError(f"Blok '{name}' must define a component")
        if isinstance(component, str):
            component = parse_bsx(component)

        resolved_dependencies = self._resolve_dependencies(dependencies)
        taken = RESERVED_ROOTS | {
            dependency.key for dependency in resolved_dependencies or ()
        }
        clashing = sorted(taken.intersection(local_state or ()))
        if clashing:
            raise ValueError(
                f"Blok '{name}' cannot keep local state under {clashing}: "
                f"the name is reserved or names a dependency"
            )
        shadowed = sorted(set(local_state or ()).intersection(demo_state or ()))
        if shadowed:
            raise ValueError(
                f"Blok '{name}' names {shadowed} in both demo_state and local_state"
            )
        # Catalog rules only: references need the dependencies that
        # ``build_declared_bloks`` infers later, and are checked there.
        for diagnostic in validate_blok_catalog(component, self.catalog_view(catalog)):
            warnings.warn(
                f"Blok {name!r}: {diagnostic.message}", CatalogWarning, stacklevel=2
            )

        self.registered_bloks[name] = BlokDeclaration(
            component=component,
            description=description,
            demo_state=demo_state,
            local_state=local_state,
            dependencies=resolved_dependencies,
            catalog=catalog,
        )

    def declare_ui_catalog(
        self,
        name: str,
        components: "Sequence[ComponentSpec] | None" = None,
        operations: "Sequence[OperationSpec] | None" = None,
        description: str | None = None,
    ) -> None:
        """Declare a UI catalog: what a renderer can draw and evaluate.

        Bloks naming this catalog are validated against it offline, at
        :meth:`register_blok` time, instead of only at registration. The same
        declaration is what a UI app uploads with ``registerUiCatalog``.

        Args:
            name: The catalog's name, as bloks and definitions refer to it.
            components: The components the UI can render.
            operations: Pure operations the UI can evaluate, extending the base catalog.
            description: What the catalog is.

        Raises:
            ValueError: If the catalog redefines a base operation, or if a different
                catalog is already declared under ``name``.
        """
        self._refuse_if_frozen(f"the catalog {name!r}")
        if not name:
            raise ValueError("A catalog name is required")

        catalog = Catalog(
            name=name,
            operations=tuple(operations or ()),
            components=tuple(components or ()),
            description=description,
        )
        check_extension_does_not_shadow_base(catalog)

        existing = self.declared_catalogs.get(name)
        if existing is not None and existing != catalog:
            raise ValueError(f"The catalog {name!r} is already declared, differently.")
        self.declared_catalogs[name] = catalog

    def catalog_view(self, name: str | None) -> "CatalogView":
        """The view a blok naming ``name`` is validated against offline.

        A declared catalog makes the operation set authoritative, so an unknown
        operation becomes a warning. Without one there is only the base catalog,
        which cannot tell a typo from an operation the UI legitimately provides,
        so unknown operations stay quiet until the server sees them.
        """
        declared = self.declared_catalogs.get(name) if name is not None else None
        if declared is None:
            return base_only_view()
        return resolve_catalogs([declared], authoritative=True)

    def _resolve_dependencies(
        self,
        dependencies: "Mapping[str, type[Any]] | Sequence[AgentDependencyInput] | None",
    ) -> list[AgentDependencyInput] | None:
        """Turn declared protocol classes into dependencies under their keys.

        Their ports were built when this app declared them.

        Raises:
            KeyError: If a class was not declared on this app.
        """
        if dependencies is None:
            return None
        if isinstance(dependencies, Mapping):
            return [
                self.structure_registry.protocol_for(cls).to_dependency_input(key)
                for key, cls in dependencies.items()
            ]
        return list(dependencies)

    def get_declared_bloks(self) -> dict[str, BlokImplementationInput]:
        """Generate blok inputs from their declarations against this registry."""
        return build_declared_bloks(self)

    # ------------------------------------------------------------------ #
    # Agent input assembly                                               #
    # ------------------------------------------------------------------ #
    def to_implement_agent_input(
        self,
        name: str | None = None,
        description: str | None = None,
    ) -> ImplementAgentInput:
        """Assemble (and validate) the full agent input from this registry.

        Constructing the :class:`ImplementAgentInput` runs the spec's structural
        rules over every port, widget and call, and
        :func:`~rekuest.definition.checks.check_agent_input` then runs the checks
        that need rekuest (search queries, state references, blok components), so
        this is the single validated retrieval point for everything the agent
        registers.

        Args:
            name: What identifies the agent. The backend falls back to the
                client id when it is omitted.
            description: What the agent is, in a sentence. Omitting it leaves
                whatever the agent already has, rather than clearing it.
        """
        from arkitekt_spec.declare.definition.checks import check_agent_input

        return check_agent_input(ImplementAgentInput(
            name=name,
            # Passed only when there is one, so that an app declaring no
            # description leaves the field *unset* rather than sending an
            # explicit null: the declaration is dumped with ``exclude_unset``,
            # so this is what keeps such an agent's wire bytes (and its
            # definition hash) exactly what they were before descriptions
            # existed.
            **({"description": description} if description is not None else {}),
            implementations=tuple(self.get_implementations()),
            states=tuple(self.states.values()),
            locks=tuple(self.get_locks()),
            bloks=tuple(self.get_declared_bloks().values()),
        ))

    # ------------------------------------------------------------------ #
    # Merging                                                            #
    # ------------------------------------------------------------------ #

    def merge(self, other: "AppRegistry", service: str | None = None) -> "AppRegistry":
        """Take everything ``other`` holds into this registry, in place.

        How a client package contributes to an app: the package owns a registry,
        declares its structures and any actions it brings into it, and an app that
        uses that service merges it. Nothing is process-wide -- a registry reaches
        only the apps that ask for its service by name.

        Args:
            other: The registry to take. It is not changed.
            service: The service ``other`` belongs to, stamped on every structure
                it brings. Without it each structure is stamped with the service
                of ``other`` whose client its expander asks for (the one that
                returns it) -- the owner knows, where the identifier only hints:
                unlok's structure is ``@lok/service`` under the service ``unlok``.

        Returns:
            This registry.

        Raises:
            RegistryFrozenError: If this registry is a running snapshot.
            ValueError: If the two claim the same interface, state, blok or
                service name. A structure clash is refused by the structure
                registry itself, which already treats an identifier as a wire
                contract.
        """
        self._refuse_if_frozen("a merged registry")

        # Services first: what they return is what the structures below inject.
        for declared in other.services.values():
            self._take_service(declared)
        for declared_provider in other.providers.values():
            self._take_provider(declared_provider)

        for interface, implementation in other.implementations.items():
            if interface in self.implementations:
                raise ValueError(
                    f"Two services both implement '{interface}'. An interface is "
                    "what a caller assigns to, so it can only mean one thing."
                )
            self.implementations[interface] = implementation
            self.declared_implementations[interface] = other.declared_implementations[
                interface
            ]

        # States are four maps that move together -- three keyed by interface and
        # one by class. Writing one and forgetting the others is the whole reason
        # this is a method and not four updates at the call site.
        for interface, state in other.states.items():
            if interface in self.states:
                raise ValueError(f"Two services both declare the state '{interface}'.")
            self.states[interface] = state
            if interface in other.state_registry_schemas:
                self.state_registry_schemas[interface] = other.state_registry_schemas[
                    interface
                ]
            if interface in other.state_interface_classes:
                cls = other.state_interface_classes[interface]
                self.state_interface_classes[interface] = cls
                self.state_classes_interfaces[cls] = interface

        for name, blok in other.registered_bloks.items():
            if name in self.registered_bloks:
                raise ValueError(f"Two services both declare the blok '{name}'.")
            self.registered_bloks[name] = blok

        for name, catalog in other.declared_catalogs.items():
            existing = self.declared_catalogs.get(name)
            if existing is not None and existing != catalog:
                raise ValueError(
                    f"Two services both declare the catalog '{name}', differently."
                )
            self.declared_catalogs[name] = catalog

        # A nested registry of three dicts, not a dict.
        for source, target in (
            (other.hooks_registry.startup_hooks, self.hooks_registry.startup_hooks),
            (other.hooks_registry.shutdown_hooks, self.hooks_registry.shutdown_hooks),
            (
                other.hooks_registry.background_worker,
                self.hooks_registry.background_worker,
            ),
        ):
            for key, hook in source.items():
                if key in target:
                    raise ValueError(f"Two services both declare the hook '{key}'.")
                target[key] = hook

        self.structure_registry.merge(
            other.structure_registry,
            service=service if service is not None else _owner_among(other.services),
        )
        return self

    # ------------------------------------------------------------------ #
    # Snapshots                                                          #
    # ------------------------------------------------------------------ #

    def snapshot(self, clients: Mapping[str, Any] | None = None) -> "AppRegistry":
        """Take what one run of this registry serves: validated, bound and frozen -- as a copy.

        This registry is the *declaration* and is never changed by running it. Each
        run takes its own snapshot, so one declaration can be run any number of
        times, concurrently too, without the runs seeing each other's clients.

        Args:
            clients: The run's clients, by service name; every declared structure
                is bound to its service's client in the snapshot. Without them
                nothing is bound yet: a runtime whose clients need the snapshot to
                be built (rekuest's agent does) binds afterwards, with
                ``snapshot.structure_registry.bind(clients)`` -- the snapshot owns
                its structure maps, so that never reaches the declaration.

        Returns:
            The snapshot: a frozen registry whose state schemas point at its own
            structure registry, and which uses the same services. A runtime builds
            its actors against that structure registry.

        Raises:
            StructureRegistryError: If a port names a structure nothing here holds.
            StructureClientError: If a declared structure's service has no client.
        """
        self.validate()
        original = self.structure_registry
        structures = (
            original.bound(clients) if clients is not None else original.copy_maps()
        )

        # Constructed, not validated: every entry was validated when it was
        # registered, and re-validating the state classes against the `AnyState`
        # protocol is something pydantic cannot do.
        snapshot = AppRegistry.model_construct(
            implementations=dict(self.implementations),
            declared_implementations=dict(self.declared_implementations),
            states=dict(self.states),
            # Every state schema is served from the copy too, merged-in ones
            # included: the registry a merged state was inspected against is a
            # package's, which no run may bind into.
            state_registry_schemas={
                key: structures for key in self.state_registry_schemas
            },
            state_interface_classes=dict(self.state_interface_classes),
            state_classes_interfaces=dict(self.state_classes_interfaces),
            registered_bloks=dict(self.registered_bloks),
            declared_catalogs=dict(self.declared_catalogs),
            hooks_registry=HooksRegistry.model_construct(
                background_worker=dict(self.hooks_registry.background_worker),
                startup_hooks=dict(self.hooks_registry.startup_hooks),
                shutdown_hooks=dict(self.hooks_registry.shutdown_hooks),
            ),
            structure_registry=structures,
            services=dict(self.services),
            providers=dict(self.providers),
        )
        snapshot.freeze()
        return snapshot

    # ------------------------------------------------------------------ #
    # Validation                                                         #
    # ------------------------------------------------------------------ #

    def validate(self) -> None:
        """Check that everything registered here can actually be served.

        Ports are built when a function is registered, so a class that was never
        registered fails there. What this catches is the other direction: a port
        naming an identifier that *this* registry cannot resolve. That happens
        when structures were declared into a different registry, or when a
        service was dropped from ``services=[...]`` after functions had already
        been registered against it -- both of which otherwise surface as a failed
        expansion, mid-assignment, long after the cause.

        Called when the app is entered, before it connects. Nothing here needs a
        connection. (An expander or an action asking for a client no registered
        service returns is refused earlier, when it is registered.)

        Raises:
            StructureRegistryError: Listing every problem it found.
        """
        registry = self.structure_registry
        resolvable = {
            PortKind.STRUCTURE: registry.identifier_structure_map,
            PortKind.MEMORY_STRUCTURE: registry.identifier_memory_structure_map,
            PortKind.MODEL: registry.identifier_model_map,
            PortKind.ENUM: registry.identifier_enum_map,
        }
        problems: list[str] = []

        def check(port: Any, where: str) -> None:
            known = resolvable.get(port.kind)
            if known is not None and port.identifier is not None:
                if port.identifier not in known:
                    kind = getattr(port.kind, "value", port.kind)
                    problems.append(
                        f"  {where}.{port.key}: nothing here holds "
                        f"'{port.identifier}' ({str(kind).lower()})"
                    )
            for child in port.children or ():
                check(child, where)

        for interface, implementation in self.implementations.items():
            definition = implementation.definition
            for port in definition.args or ():
                check(port, f"{interface}(arg)")
            for port in definition.returns or ():
                check(port, f"{interface}(return)")

        for interface, state in self.states.items():
            for port in state.definition.ports or ():
                check(port, f"state {interface}")

        # A blok may depend on an action another app provides. Those ports were
        # built here too (at `register_blok`), so they name this registry's
        # structures -- and they are the likeliest to drift, being about types
        # this app does not implement itself.
        for name, blok in self.registered_bloks.items():
            for dependency in blok.dependencies or ():
                for action in dependency.action_dependencies or ():
                    demand = getattr(action, "demand", None)
                    if demand is None:
                        continue
                    for port in demand.arg_matches or ():
                        check(port, f"blok {name!r} -> {action.key}(arg)")
                    for port in demand.return_matches or ():
                        check(port, f"blok {name!r} -> {action.key}(return)")

        if problems:
            raise StructureRegistryError(
                "This app is not valid:\n"
                + "\n".join(sorted(problems))
                + "\nA structure is declared by the service that owns it, so the "
                "usual cause is a service this app does not register "
                "(`App(services=[mikro_service])`), or a registry filled by a "
                "different app. Expanding one of these would fail mid-assignment "
                "instead."
            )

    # ------------------------------------------------------------------ #
    # Decorators                                                         #
    # ------------------------------------------------------------------ #
    @overload
    def state(
        self,
        cls: type[T],
        /,
        *,
        name: str | None = None,
        required_locks: list[str] | None = None,
    ) -> type[T]: ...

    @overload
    def state(
        self,
        /,
        *,
        name: str | None = None,
        required_locks: list[str] | None = None,
    ) -> Callable[[type[T]], type[T]]: ...

    @dataclass_transform(field_specifiers=(model_field,))
    def state(
        self,
        *args: type[T],
        name: str | None = None,
        required_locks: list[str] | None = None,
    ) -> type[T] | Callable[[type[T]], type[T]]:
        """Register a class as a stateful entity (it becomes a dataclass).

        Takes everything the underlying decorator does; they used to drift, and a
        state declaring ``required_locks`` through this method lost them silently.
        """
        from arkitekt_spec.declare.state.decorator import declare_state

        return declare_state(
            *args,
            name=name,
            required_locks=required_locks,
            registry=self,
            structure_reg=self.structure_registry,
        )

    def background(
        self,
        func: T | None = None,
        /,
        *,
        name: str | None = None,
    ) -> T | Callable[[T], T]:
        """Register a background task."""
        from arkitekt_spec.declare.agents.hooks.declare import declare_background

        return declare_background(  # type: ignore[return-value]
            func,  # type: ignore[arg-type]
            name=name,
            registry=self.hooks_registry,
            structure_registry=self.structure_registry,
        )

    def startup(
        self,
        func: T | None = None,
        /,
        *,
        name: str | None = None,
    ) -> T | Callable[[T], T]:
        """Register a startup hook."""
        from arkitekt_spec.declare.agents.hooks.declare import declare_startup

        return declare_startup(  # type: ignore[return-value]
            func,  # type: ignore[arg-type]
            name=name,
            registry=self.hooks_registry,
            structure_registry=self.structure_registry,
        )

    def shutdown(
        self,
        func: T | None = None,
        /,
        *,
        name: str | None = None,
    ) -> T | Callable[[T], T]:
        """Register a shutdown hook."""
        from arkitekt_spec.declare.agents.hooks.declare import declare_shutdown

        return declare_shutdown(  # type: ignore[return-value]
            func,  # type: ignore[arg-type]
            name=name,
            registry=self.hooks_registry,
            structure_registry=self.structure_registry,
        )

    def context(
        self,
        *args: type[T],
        name: str | None = None,
        locks: list[str] | None = None,
    ) -> type[T] | Callable[[type[T]], type[T]]:
        """Declare a context class of this app: ``@app.context``.

        The registry surface of
        :meth:`~rekuest.structures.registry.StructureRegistry.context`: the class
        is returned unchanged and what this app knows about it -- the name the
        agent keeps it under, the locks its use requires -- is kept on this app's
        structures.

        Args:
            *args: The class, when used without parentheses.
            name: The name the agent keeps the context under. Defaults to the
                snake_case class name.
            locks: Lock names required while a parameter of this class is held.

        Returns:
            The class, or a class decorator returning it.
        """
        for cls in args:
            self._refuse_if_frozen(f"the context {cls.__qualname__}")
        if args:
            return self.structure_registry.context(*args, name=name, locks=locks)

        def decorator(cls: type[T]) -> type[T]:
            self._refuse_if_frozen(f"the context {cls.__qualname__}")
            return self.structure_registry.context(cls, name=name, locks=locks)

        return decorator

    def structure(
        self,
        identifier: str,
        *,
        cls: type[object] | None = None,
        widget: AssignWidgetInput | None = None,
        description: str | None = None,
        expand_many: ManyExpander | None = None,
        shrink: Shrinker | None = None,
        returnwidget: ReturnWidgetInput | None = None,
    ) -> Callable[[ExpanderT], ExpanderT]:
        """Declare a type that travels by id: ``@registry.structure("@mikro/image")``.

        The registry surface of
        :meth:`~rekuest.structures.registry.StructureRegistry.structure`, so a
        client package declares its structures and its actions into one object.
        The expander's return annotation is the class that travels::

            @registry.structure("@mikro/tabledataset", shrink=shrink_table)
            async def expand_table(id: str, mikro: Mikro) -> TableDataset:
                return await mikro.aget_table_dataset(id)

        Args:
            identifier: What it travels as, ``@package/key``.
            cls: The class that travels, when the expander's return annotation
                is not it (``Any``, a union).
            widget: The widget a user picks one with.
            description: What it is, for the UI.
            expand_many: Fetches several ids at once, when the client's own batch
                call is not the right answer.
            shrink: Turns the object into its id. Defaults to reading ``.id``.
                It may ask for clients by annotation after the object, like the
                expander.
            returnwidget: The widget one is shown with.

        Returns:
            A decorator returning the expander unchanged, with its declared type.
        """
        return self.structure_registry.structure(
            identifier,
            cls=cls,
            widget=widget,
            description=description,
            expand_many=expand_many,
            shrink=shrink,
            returnwidget=returnwidget,
        )

    def app_context(
        self,
        *args: type[T],
        name: str | None = None,
    ) -> type[T] | Callable[[type[T]], type[T]]:
        """Declare the class of this app's app context: ``@app.app_context``.

        The app context is the object the caller hands to ``run(context=...)``;
        a hook annotated with the class is handed it. The class is returned
        unchanged; the declaration is kept on this app's structures.

        Args:
            *args: The class, when used without parentheses.
            name: What the app context is called. Defaults to the class name.

        Returns:
            The class, or a class decorator returning it.
        """
        for cls in args:
            self._refuse_if_frozen(f"the app context {cls.__qualname__}")
        if args:
            return self.structure_registry.app_context(*args, name=name)

        def decorator(cls: type[T]) -> type[T]:
            self._refuse_if_frozen(f"the app context {cls.__qualname__}")
            return self.structure_registry.app_context(cls, name=name)

        return decorator

    @property
    def app_context_class(self) -> type[Any] | None:
        """The class a run's ``context=`` must be an instance of, or ``None``."""
        return self.structure_registry.app_context_class

    def require_app_context(self, value: Any, *, whose: str) -> None:
        """Refuse a run whose ``context=`` does not fit what this app declared.

        Called by whatever starts the agent (a run, a served FastAPI app, the
        agent itself), before anything else happens, so a missing or wrong
        context fails at the call rather than inside a hook.

        Args:
            value: What the run was given as its context.
            whose: How to name the app in the error ("App 'x'").

        Raises:
            AppContextError: If the app declares a class and ``value`` is not an
                instance of it, or declares none and ``value`` is not ``None``.
        """
        declared = self.app_context_class
        if declared is None:
            if value is not None:
                raise AppContextError(
                    f"{whose} declares no app context, but was given a "
                    f"{type(value).__name__}. Declare it (App(..., app_context="
                    f"{type(value).__name__}) or registry.app_context("
                    f"{type(value).__name__})) or pass no context."
                )
            return
        if value is None:
            raise AppContextError(
                f"{whose} declares an app context of class {declared.__name__}, "
                f"but none was given. Pass one: run(app, context={declared.__name__}(...))."
            )
        if not isinstance(value, declared):
            raise AppContextError(
                f"{whose} declares an app context of class {declared.__name__}, "
                f"but was given a {type(value).__name__}."
            )

    @overload
    def model(
        self,
        cls: type[T],
        /,
        *,
        identifier: str | None = None,
        description: str | None = None,
    ) -> type[T]: ...

    @overload
    def model(
        self, /, *, identifier: str | None = None, description: str | None = None
    ) -> Callable[[type[T]], type[T]]: ...

    @dataclass_transform(field_specifiers=(model_field,))
    def model(
        self,
        *args: type[T],
        identifier: str | None = None,
        description: str | None = None,
    ) -> type[T] | Callable[[type[T]], type[T]]:
        """Declare a model of this app: a class whose instances travel by value.

        The registry surface of
        :meth:`~rekuest.structures.registry.StructureRegistry.model`: the class
        is made a dataclass if it is not one and registered on this app's
        structures. A port annotated with it has one child per field::

            @app.model
            class AcquisitionConfig:
                threshold: float = model_field(default=0.5)

        Args:
            *args: The class, when used without parentheses.
            identifier: What it travels as, ``@package/key``. Defaults to
                ``@<the class's module>/<snake_case class name>``.
            description: What it is, for the UI. Defaults to the class docstring.

        Returns:
            The class, or a class decorator returning it.
        """
        for cls in args:
            self._refuse_if_frozen(f"the model {cls.__qualname__}")
        if args:
            return self.structure_registry.model(
                *args, identifier=identifier, description=description
            )

        def decorator(cls: type[T]) -> type[T]:
            self._refuse_if_frozen(f"the model {cls.__qualname__}")
            return self.structure_registry.model(
                cls, identifier=identifier, description=description
            )

        return decorator

    def register_memory_structure(
        self,
        cls: type[Any],
        identifier: str | None = None,
        description: str | None = None,
    ) -> None:
        """Register a class whose instances stay on this agent's shelve.

        Nothing is registered automatically, so a port may only name a class that
        was registered first. This is how you say "an object of this kind is
        passed around by reference, and never leaves this process":

        ```python
        app.register_memory_structure(np.ndarray)

        @app.register
        def blur(image: np.ndarray) -> np.ndarray: ...
        ```

        Args:
            cls: The class to keep on the shelve.
            identifier: What to call it. Defaults to a name derived from the
                class (``numpy.ndarray`` -> ``@numpy/ndarray``).
            description: What it is, for the UI.
        """
        self._refuse_if_frozen(f"the memory structure {getattr(cls, '__name__', cls)}")
        self.structure_registry.register_as_memory_structure(
            cls, identifier=identifier, description=description
        )

    def register_action(
        self,
        func: Callable[P, R],
        /,
        *,
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
    ) -> "WrappedFunction[P, R]":
        """Register a function as an action, as a plain call rather than a decorator.

        Reads better where actions are declared conditionally or in a loop:

        ```python
        for line in ("488", "561"):
            app.register_action(make_setter(line), key=f"set_laser_{line}")
        ```

        Args:
            func: The function or actor to offer.
            key: What the server identifies the action by (with ``version``).
                Defaults to the function name, so functions built by one factory
                need distinct keys. Also the default ``interface``.
            name: Display name. Defaults to the function name.
            description: Description. Defaults to the docstring.
            actifier: A runtime's actifier, for actions a particular kind of actor
                must run (Qt, fluss). ``None`` leaves it to the runtime.
            interface: Interface the action is offered at. Defaults to ``key``,
                else one derived from the function name.
            stateful: Mark the definition stateful (set automatically when it
                uses states).
            widgets: Widgets per argument.
            collections: Collections the action is grouped into.
            port_groups: Port group assignments.
            port_effects: UI effects per port (hide, disable, … as its values change).
            is_test_for: Actions this one tests.
            validators: Input validation rules per argument.
            optimistics: Optimistic outputs.
            in_process: Run in the event loop instead of a worker thread.
            tracks: Tracks the implementation follows.
            locks: Locks held while an assignment runs.
            concurrency: Whether assignments may run concurrently.
            policy: What happens to a running assignment when its caller
                disconnects.
            version: Version of the definition.
            catalogs: Catalogs the action is listed in.
            effects: What running it again would do to the world. Informational;
                None takes the app's default.
            execution: ``Execution.WORKFLOW`` to call other actions and be resumed
                after a crash (see :meth:`register_workflow`).

        Returns:
            The function, still callable as itself.
        """
        from arkitekt_spec.declare.register import declare_implementation

        return declare_implementation(
            func,
            implementation_registry=self,
            structure_registry=self.structure_registry,
            key=key,
            name=name,
            description=description,
            actifier=actifier,
            interface=interface,
            stateful=stateful,
            widgets=widgets,
            collections=collections,
            port_groups=port_groups,
            port_effects=port_effects,
            is_test_for=is_test_for,
            validators=validators,
            optimistics=optimistics,
            in_process=in_process,
            tracks=tracks,
            locks=locks,
            concurrency=concurrency,
            policy=policy,
            version=version,
            catalogs=catalogs,
            effects=effects,
            execution=execution,
        )

    def register_workflow(
        self,
        func: Callable[P, R],
        /,
        **kwargs: Any,
    ) -> "WrappedFunction[P, R]":
        """Register a function as a workflow: an action that may call other actions.

        Called like any action. When its agent dies it is resumed, not lost: calls
        it already made return their recorded results, and values it took through
        the task (``task.now()``, ``task.record(...)``) come back the same. So its
        code must be deterministic. Takes the keyword arguments of
        :meth:`register_action`.
        """
        return self.register_action(func, execution=Execution.WORKFLOW, **kwargs)

    def register(
        self,
        func: T | None = None,
        /,
        **kwargs: Any,
    ) -> T | Callable[[T], T]:
        """Register a function or class as an implementation.

        Bare (``app.register(fn)``) or configured
        (``app.register(interface=...)(fn)``); the registry and its structures are
        supplied here, which is the whole reason this wrapper exists.
        """
        from arkitekt_spec.declare.register import declare_implementation

        return declare_implementation(  # type: ignore[return-value]
            func,  # type: ignore[arg-type]
            implementation_registry=self,
            structure_registry=self.structure_registry,
            **kwargs,
        )


__all__ = ["AppRegistry"]
