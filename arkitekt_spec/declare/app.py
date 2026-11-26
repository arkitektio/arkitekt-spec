"""Global App Registry for Rekuest.

This module provides a single unified registry that holds every piece of agent
registration data — implementations, states and bloks — together with the hooks
and structure registries.
"""

import hashlib
import inspect
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace as dataclass_replace
from pathlib import Path, PurePosixPath
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
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, SkipValidation

from arkitekt_spec.actions import (
    ASSET_SOURCE_MAX_LENGTH,
    ArgPortInput,
    AssetInput,
    AssetKind,
    AssignWidgetInput,
    BlokDemoInput,
    BlokImplementationInput,
    ComponentContext,
    ComponentNodeInput,
    Effects,
    Execution,
    ImplementAgentInput,
    ImplementationInput,
    LockDefinitionInput,
    LockImplementationInput,
    PortGroupInput,
    PortKind,
    ReturnPortInput,
    StateImplementationInput,
    TestTargetInput,
    ValidatorInput,
    is_asset_source,
)
from arkitekt_spec.blok.bsx import parse_bsx, source_catalog
from arkitekt_spec.blok.catalogs import (
    BlokView,
    blok_view,
    catalog_chain,
    default_view,
    known_catalog,
)
from arkitekt_spec.blok.declared import blok_catalog, blok_header
from arkitekt_spec.blok.vocabulary import VOCAB, BlokError, Finding, load_vocabulary
from arkitekt_spec.declare.actors.policy import KEEP, DisconnectPolicy
from arkitekt_spec.declare.actors.types import Actifier, DeclaredImplementation
from arkitekt_spec.declare.agents.hooks.registry import HooksRegistry
from arkitekt_spec.declare.blok.action import ActionBloks, action_blok_components_in
from arkitekt_spec.declare.blok.declarations import (
    demo_input,
)
from arkitekt_spec.declare.blok.origin import (
    DeclaredAt,
    declared_at,
    finding_key,
    warn_at,
)
from arkitekt_spec.declare.blok.port import check_port_blok_reads
from arkitekt_spec.declare.blok.registry import as_registered, build_declared_bloks
from arkitekt_spec.declare.blok.validate import BlokScope, check_blok_tree
from arkitekt_spec.declare.catalogs import (
    BASE_CATALOG_ID,
    BASE_CATALOG_NAME,
    MODEL_REF_PATTERN,
    WARNING,
    BlokCheckMode,
    Catalog,
    CatalogView,
    ComponentSpec,
    Diagnostic,
    OperationSpec,
    check_extension_does_not_shadow_base,
    resolve_catalogs,
)
from arkitekt_spec.declare.client import ClientDeclaration
from arkitekt_spec.declare.coercible_types import OptimisticCoercible
from arkitekt_spec.declare.definition.catalogs import (
    validate_definition_against_catalog,
    widget_tree_findings,
)
from arkitekt_spec.declare.definition.define import convert_object_to_argport
from arkitekt_spec.declare.errors import AppContextError, RegistryFrozenError
from arkitekt_spec.declare.interactions import DeclaredInteractions
from arkitekt_spec.declare.protocol.types import AnyState
from arkitekt_spec.declare.provider import Provider
from arkitekt_spec.declare.structures.errors import StructureRegistryError
from arkitekt_spec.declare.structures.model import model_field
from arkitekt_spec.declare.structures.registry import StructureRegistry
from arkitekt_spec.declare.structures.types import (
    StateDeclaration,
)
from arkitekt_spec.inspection import InspectedAsset
from arkitekt_spec.scalars import JSONSerializable

T = TypeVar("T")
P = ParamSpec("P")
R = TypeVar("R")

if TYPE_CHECKING:
    from arkitekt_spec.declare.register import WrappedFunction


def _function_origin(function: Callable[..., object]) -> DeclaredAt | None:
    """The line ``function`` is defined on, where a warning about it points; ``None`` for a callable object."""
    code = getattr(inspect.unwrap(function), "__code__", None)
    if code is None:
        return None
    return DeclaredAt(
        filename=code.co_filename,
        lineno=code.co_firstlineno,
        module=str(getattr(function, "__module__", "")),
    )


class BlokDeclaration(BaseModel):
    """Everything :meth:`AppRegistry.register_blok` records for one blok."""

    component: ComponentNodeInput
    source: str | None = None
    """The BSX the component was parsed from, which is what is sent: a third of the tree's bytes."""
    description: str | None = None
    demo: BlokDemoInput | None = None
    kind: str = "agent"
    """What its ``<Blok kind>`` says: ``agent``, or ``action`` for an action's logo."""
    catalog: str | None = None
    """The catalog its ``<Blok catalog>`` names; None: the base catalog."""
    context: ComponentContext = ComponentContext.PANEL
    default: bool = False
    for_model: str | None = None
    for_node: str | None = None
    args: tuple[ArgPortInput, ...] = ()
    """The items the blok is opened for, read under ``args.<key>``."""
    mode: BlokCheckMode = "warning"
    """How a catalog finding, or a query its service's schema does not check, is reported."""
    declared_at: DeclaredAt | None = None
    """The app's line that declared it: where its warnings point."""
    file: str | None = None
    """The ``.bsx`` file it was read from, absolute."""
    reported: frozenset[str] = frozenset()
    """The warnings already told when it was declared (:func:`~arkitekt_spec.declare.blok.origin.finding_key`)."""


#: How each kind of asset is recognised: the extensions it is written with, and how
#: its first bytes must start (a WebP also says so at offset 8).
_ASSET_FORMATS: dict[AssetKind, tuple[frozenset[str], tuple[bytes, ...]]] = {
    AssetKind.MODEL_3D: (frozenset({".glb"}), (b"glTF",)),
    AssetKind.IMAGE: (
        frozenset({".png", ".jpg", ".jpeg", ".webp"}),
        (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"RIFF"),
    ),
}


def _asset_kind(path: Path, data: bytes, kind: AssetKind | None) -> AssetKind:
    """The kind of the file at ``path``: the one given, or the one its extension says.

    Raises:
        ValueError: If no kind is given and the extension names none, or the file
            does not start as a file of that kind does.
    """
    if kind is None:
        kind = next(
            (
                candidate
                for candidate, (extensions, _) in _ASSET_FORMATS.items()
                if path.suffix.lower() in extensions
            ),
            None,
        )
        if kind is None:
            known = sorted(
                ext for extensions, _ in _ASSET_FORMATS.values() for ext in extensions
            )
            raise ValueError(
                f"Cannot tell what kind of asset '{path.name}' is: say it with kind=, "
                f"or use one of {known}"
            )
    kind = AssetKind(kind)
    starts = _ASSET_FORMATS[kind][1]
    if not data.startswith(starts) or (
        data.startswith(b"RIFF") and data[8:12] != b"WEBP"
    ):
        raise ValueError(f"'{path.name}' is not a valid {kind.label} file")
    return kind


def _sourced_asset(
    name: str, source: str, ref: str | None, kind: AssetKind | None
) -> "AssetDeclaration":
    """The declaration of a file the app does not hold: where to fetch it, and its hash.

    Nothing is fetched, so the kind is told from the extension the URL's path ends
    in (a query string does not disturb it) and the bytes are the server's to check.

    Raises:
        ValueError: If the source is not an https URL, the reference is missing or
            malformed, or no kind is given and the URL's extension names none.
    """
    if not is_asset_source(source):
        raise ValueError(
            f"The asset '{name}': a source is an https URL of at most "
            f"{ASSET_SOURCE_MAX_LENGTH} characters, not {source!r}"
        )
    if ref is None or not MODEL_REF_PATTERN.fullmatch(ref):
        raise ValueError(
            f"The asset '{name}' names a source, so it needs the hash of the file "
            f"there: ref='sha256:' and 64 lowercase hex characters, not {ref!r}"
        )
    if kind is None:
        suffix = PurePosixPath(urlparse(source).path).suffix.lower()
        kind = next(
            (
                candidate
                for candidate, (extensions, _) in _ASSET_FORMATS.items()
                if suffix in extensions
            ),
            None,
        )
        if kind is None:
            raise ValueError(
                f"Cannot tell what kind of asset '{name}' is from {source!r}: "
                f"say it with kind="
            )
    return AssetDeclaration(name=name, kind=AssetKind(kind), ref=ref, source=source)


class AssetDeclaration(BaseModel):
    """A file :meth:`AppRegistry.register_asset` recorded: what the server is told, and where it is."""

    name: str
    kind: AssetKind
    ref: str
    size: int | None = None
    """The file's size; unknown for a file the app does not hold."""
    path: Path | None = None
    """Where the file is on this machine. Never sent. None for a file the app does not hold."""
    source: str | None = None
    """The https URL a file the app does not hold is fetched from."""

    @property
    def where(self) -> str:
        """The file as it is named to a person: its file name, or its source."""
        return self.path.name if self.path is not None else str(self.source)

    def as_input(self) -> AssetInput:
        """What the server is told: the size of a held file, the source of one that is not."""
        said: dict[str, Any] = (
            {"size": self.size} if self.source is None else {"source": self.source}
        )
        return AssetInput(name=self.name, kind=self.kind, ref=self.ref, **said)


DYNAMIC_ASK_SCOPE = "rekuest_dynamic_ask"
"""The scope a task's agent needs to ask through a blok made at ask time (``dynamic_asks``)."""
DYNAMIC_CALL_SCOPE = "rekuest_dynamic_call"
"""The scope a task's agent needs to call an action found at run time (``dynamic_calls``)."""
DYNAMIC_INFORM_SCOPE = "rekuest_dynamic_inform"
"""The scope a task's agent needs to show a blok made at inform time (``dynamic_informs``)."""
DEPENDENCY_CALL_SCOPE = "rekuest_dependency_call"
"""The scope a task's agent needs to call an action through a dependency its action declares."""


def _refuse_unpersistable(
    state: StateImplementationInput, persist: Literal["synchronous"] | float | None
) -> None:
    """``persist`` is ``"synchronous"`` or a period, on a state that can outlive its process.

    A memory structure cannot: its drawer dies with the process, and its reference would
    expand to a dead id without any complaint.
    """
    if persist is None:
        return
    if persist != "synchronous" and (
        isinstance(persist, bool)
        or not isinstance(persist, (int, float))
        or persist <= 0
    ):
        raise ValueError(
            f"The state '{state.interface}' has persist={persist!r}: use \"synchronous\" "
            "or a period in seconds above 0."
        )

    def in_memory(port: ReturnPortInput) -> bool:
        return port.kind == PortKind.MEMORY_STRUCTURE or any(
            in_memory(child) for child in port.children or ()
        )

    held = [port.key for port in state.definition.ports if in_memory(port)]
    if held:
        raise ValueError(
            f"The state '{state.interface}' cannot be persisted: {held} hold memory "
            "structures, which die with the process."
        )


def check_app_context(declared: type[Any] | None, value: object, *, whose: str) -> None:
    """Refuse a run whose ``context=`` does not fit the app-context class ``declared``.

    Raises:
        AppContextError: If ``value`` is not an instance of ``declared``, or is given
            when nothing is declared.
    """
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

    # --- bloks (formerly BlokRegistry) ---
    registered_bloks: dict[str, "BlokDeclaration"] = Field(default_factory=dict)
    registered_assets: dict[str, "AssetDeclaration"] = Field(default_factory=dict)
    """The files the app brings itself, by name: a run uploads what the server lacks."""
    root: Path | None = Field(default=None, exclude=True)
    """The app's directory, which asset paths are relative to. The working directory when unset."""
    declared_catalogs: dict[str, Catalog] = Field(default_factory=dict)

    # --- other registries kept as composed fields ---
    hooks_registry: HooksRegistry = Field(default_factory=HooksRegistry)
    structure_registry: StructureRegistry = Field(default_factory=StructureRegistry)

    # --- clients ---
    clients: dict[str, ClientDeclaration[Any]] = Field(
        default_factory=dict, exclude=True
    )
    """The clients this app uses, by the name a run keeps each under: what a run
    builds (or is handed) before anything else. Set when the app is assembled."""
    provider: Provider[Any] | None = Field(default=None, exclude=True)
    """What builds the agent serving this app's offerings, if anything does."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # ------------------------------------------------------------------ #
    # Clients                                                            #
    # ------------------------------------------------------------------ #

    def use_client(self, client: ClientDeclaration[Any]) -> None:
        """Use ``client``: a parameter annotated with its class is handed the run's instance.

        Raises:
            ValueError: If another client is used under the same name.
        """
        self._refuse_if_frozen(f"the client '{client.name}'")
        existing = self.clients.get(client.name)
        if existing is not None and existing is not client:
            raise ValueError(
                f"Two clients are both called '{client.name}' ({existing.cls.__qualname__} "
                f"and {client.cls.__qualname__}). A name is what a run keeps a client "
                "under: give one its own with @client(name=...)."
            )
        self.clients[client.name] = client
        self.structure_registry.clients[client.name] = client.cls

    def use_provider(self, provider: Provider[Any]) -> None:
        """Serve this app through ``provider``: an app is served by one agent.

        Raises:
            ValueError: If a different provider is used already.
        """
        self._refuse_if_frozen(f"the provider '{provider.name}'")
        if self.provider is not None and self.provider is not provider:
            raise ValueError(
                f"An app is served by one agent, and '{self.provider.name}' and "
                f"'{provider.name}' both build one."
            )
        self.provider = provider

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
        :meth:`StructureRegistry.declare <arkitekt_spec.declare.structures.registry.StructureRegistry.declare>`.
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

    def interactions(self, cls: type[T]) -> type[T]:
        """Declare an interactions protocol: ``@app.interactions`` on a class of ``@ask`` and
        ``@inform`` methods.

        Every method's ports are built and its blok checked now, against this app's structures
        and catalogs, and the class is returned unchanged. An action parameter annotated with
        it is handed a proxy whose methods ask a person or show them a blok, and the action
        registers each as one of its asks or informs (see :mod:`arkitekt_spec.declare.interactions`).
        """
        self.register_interactions(cls)
        return cls

    def register_interactions(
        self, cls: type[Any], at: DeclaredAt | None = None
    ) -> None:
        """Declare the interactions protocol ``cls`` here (the twin of :meth:`interactions`).

        Raises:
            DefinitionError: If a method is neither an ask nor an inform, or its blok breaks a rule.
        """
        self._refuse_if_frozen(f"the interactions protocol {cls.__qualname__}")
        self.structure_registry.interactions[cls] = DeclaredInteractions(cls, self, at)

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

    def get_scopes(self) -> list[str]:
        """The scopes what is declared here needs beyond an agent's own:
        ``rekuest_dependency_call`` when an action calls through a dependency it declares,
        ``rekuest_dynamic_call`` when one calls actions found at run time,
        ``rekuest_dynamic_ask`` when one asks through bloks made at ask time and
        ``rekuest_dynamic_inform`` when one shows bloks made at inform time. The server
        refuses each without it."""
        scopes: set[str] = set()
        for implementation in self.implementations.values():
            if any(
                dependency.action_dependencies
                for dependency in implementation.dependencies or ()
            ):
                scopes.add(DEPENDENCY_CALL_SCOPE)
            if implementation.dynamic_asks:
                scopes.add(DYNAMIC_ASK_SCOPE)
            if implementation.dynamic_calls:
                scopes.add(DYNAMIC_CALL_SCOPE)
            if implementation.dynamic_informs:
                scopes.add(DYNAMIC_INFORM_SCOPE)
        return sorted(scopes)

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
        persist: Literal["synchronous"] | float | None = None,
    ) -> None:
        """Register a state schema at its interface, with the rules for changing it.

        The class is left as it is; what this app knows about it is recorded
        here and on the structure registry (:attr:`StructureRegistry.states`),
        which is what tells an action parameter apart as this state.

        Args:
            cls: The state class.
            state: Its schema, built against ``registry``.
            registry: The structures its ports were built against.
            required_locks: Locks every action taking it holds (inferred). Not a
                write guard.
            persist: Keep its last value on disk for a startup hook of the next run:
                ``"synchronous"`` (every change, an fsync on the event loop) or a
                period in seconds. ``None`` keeps nothing.
        """
        self._refuse_if_frozen(f"the state '{state.interface}'")
        _refuse_unpersistable(state, persist)
        if state.interface in load_vocabulary().reserved_state_names:
            raise ValueError(
                f"A state cannot be called '{state.interface}': it is a segment of an agent in a blok "
                f"(self.tasks.<action>, self.assets.<name>, self.id, self.name) [deps.reserved_state]"
            )
        self.states[state.interface] = state
        self.state_registry_schemas[state.interface] = registry
        self.state_interface_classes[state.interface] = cls
        declaration = StateDeclaration(
            interface=state.interface,
            definition=state.definition,
            required_locks=tuple(required_locks or ()),
            persist=persist,
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

    # ------------------------------------------------------------------ #
    # Blok storage                                                       #
    # ------------------------------------------------------------------ #
    def register_blok(
        self,
        name: str,
        component: str | Path | ComponentNodeInput | None = None,
        description: str | None = None,
        *,
        demo: "BlokDemoInput | Mapping[str, JSONSerializable] | None" = None,
        default: bool = False,
        for_model: str | None = None,
        for_node: str | None = None,
        args: "Mapping[str, Any] | None" = None,
        mode: BlokCheckMode = "warning",
        at: DeclaredAt | None = None,
    ) -> None:
        """Register a blok of this app's agent (blok language v2).

        The blok is one ``<Blok kind="agent" context="panel|scene|logo">``: what it owns
        (``<local>``, ``<query>``, ``<subscription>``) is declared inside it. It reads its own
        agent as ``self`` -- ``self.<state>.<field>``, ``self.tasks.<action>``,
        ``self.assets.<name>`` -- and calls it as ``self.<action>(name: value) into <record>``;
        what it reads of ``self`` becomes the demands of its one dependency when the agent's
        input is built. It is checked now against the language (roots, callbacks, ids, ...)
        and the catalog its ``<Blok catalog>`` names; a catalog finding is a
        :class:`~arkitekt_spec.declare.catalogs.CatalogWarning` unless ``mode`` is ``"strict"``.
        An action's logo (``<Blok kind="action" context="logo">``, painted with the pens only,
        reading only ``task``) is registered here too, and named by the action's ``logo=``.

        Args:
            name: The blok's name.
            component: The tree, as v2 BSX or already parsed: a ``<Blok>``. A ``Path`` is a
                ``.bsx`` file, relative to :attr:`root`; an error raised for it carries it as
                ``bsx_file``.
            description: What the blok shows.
            demo: What a preview renders it with, mirroring the roots it reads:
                ``{"deps": {"self": {...}}, "args": {...}, "queries": {...}}``. Placeholders
                for the states of ``self`` it reads when left out.
            default: Whether this is the app's default blok for its context.
            for_model: The model (``"sha256:..."``) this blok decorates. Panel bloks only.
            for_node: The node of ``for_model`` to attach to.
            args: The items the blok is opened for, typed like an action's arguments and
                read under ``args.<key>``. Panel bloks only.
            mode: ``"warning"`` or ``"strict"`` (see above).

        Raises:
            ValueError: If the name or component is missing, the BSX does not parse
                (:class:`~arkitekt_spec.blok.vocabulary.BlokError`, with its code), the tree
                breaks a rule of the language, or the declaration contradicts itself.
        """
        self._refuse_if_frozen(f"the blok {name!r}")
        if not name:
            raise ValueError("A blok name is required")
        if component is None:
            raise ValueError(f"Blok '{name}' must define a component")
        at = at or declared_at()
        if isinstance(component, Path):
            file = ((self.root or Path.cwd()) / component).resolve()
            try:
                self._register_blok(
                    name,
                    file.read_text(encoding="utf-8"),
                    description,
                    demo=demo,
                    default=default,
                    for_model=for_model,
                    for_node=for_node,
                    args=args,
                    mode=mode,
                    at=at,
                    file=str(file),
                )
            except Exception as error:
                error.bsx_file = str(file)  # type: ignore[attr-defined]
                raise
            return
        self._register_blok(
            name,
            component,
            description,
            demo=demo,
            default=default,
            for_model=for_model,
            for_node=for_node,
            args=args,
            mode=mode,
            at=at,
        )

    def _register_blok(
        self,
        name: str,
        component: str | ComponentNodeInput,
        description: str | None,
        *,
        demo: "BlokDemoInput | Mapping[str, JSONSerializable] | None",
        default: bool,
        for_model: str | None,
        for_node: str | None,
        args: "Mapping[str, Any] | None",
        mode: BlokCheckMode,
        at: DeclaredAt | None,
        file: str | None = None,
    ) -> None:
        source: str | None = None
        try:
            catalog = (
                source_catalog(component)
                if isinstance(component, str)
                else blok_catalog(component)
            )
            view = self.blok_view(catalog)
            if isinstance(component, str):
                source = component
                component = parse_bsx(component, view=view)
            header = blok_header(component)
        except BlokError as error:
            raise error.within(f"Blok {name!r}") from None
        context = ComponentContext(header.context)
        if header.kind == "action" and context != ComponentContext.LOGO:
            raise ValueError(
                f"Blok '{name}' is an action's {context.value.lower()} blok: it goes with its action (@app.workflow(bloks=...)), not app.blok"
            )
        reading = action_blok_components_in(component)
        if reading:
            raise ValueError(
                f"Blok '{name}': {reading[0].component!r} ({reading[0].id}) reads a run of an action, so only "
                f"an action's own blok holds one (@app.workflow(bloks=...))"
            )
        arg_ports = tuple(
            convert_object_to_argport(cls, key, self.structure_registry)
            for key, cls in (args or {}).items()
        )
        if arg_ports and context != ComponentContext.PANEL:
            raise ValueError(
                f"Blok '{name}': only a panel blok is opened for an item, so only it takes args"
            )
        if for_node is not None and for_model is None:
            raise ValueError(f"Blok '{name}' names for_node without a for_model")
        if for_model is not None:
            if not MODEL_REF_PATTERN.fullmatch(for_model):
                raise ValueError(
                    f"Blok '{name}': for_model must be 'sha256:' and 64 lowercase hex characters, not {for_model!r}"
                )
            if context != ComponentContext.PANEL:
                raise ValueError(
                    f"Blok '{name}': only a panel blok can decorate a model"
                )
        declaration = BlokDeclaration(
            component=component,
            source=source,
            description=description,
            demo=demo_input(demo),
            kind=header.kind,
            catalog=catalog,
            context=context,
            default=default,
            for_model=for_model,
            for_node=for_node,
            args=arg_ports,
            mode=mode,
            declared_at=at,
            file=file,
        )
        # What the server checks of the declarations alone (one way sent, names, documents' kinds).
        BlokImplementationInput(
            key=name,
            vocab=VOCAB,
            components=(component,),
            context=context,
            demo=declaration.demo,
            catalog=catalog,
            args=arg_ports or None,
        )
        from arkitekt_spec.declare.blok.registry import (
            check_catalog_target,
            checked_view,
            self_arg_ports,
        )

        # ``self`` is open here (not mounted yet): what it reads is checked when the agent's input
        # is built, against the demands that reading infers. A local's ``like`` borrows from the
        # actions registered by now.
        scope = BlokScope(
            kinds=("agent", "action"),
            mount="app.blok",
            args=tuple(port.key for port in arg_ports),
            demo=declaration.demo,
            arg_ports=self_arg_ports(self),
        )
        reported: set[str] = set()
        for finding in (
            *check_catalog_target(catalog, self),
            *check_blok_tree(component, scope, checked_view(view)),
        ):
            if finding.level == "error" or mode == "strict":
                where = f" ({finding.path})" if finding.path else ""
                raise BlokError.from_finding(
                    dataclass_replace(
                        finding,
                        path=None,
                        message=f"Blok {name!r}{where}: {finding.message}",
                    ),
                    node=finding.path,
                )
            key = finding_key(finding)
            if key not in reported:
                reported.add(key)
                warn_at(
                    f"Blok {name!r}: {finding.message} [{finding.code}]",
                    at,
                    finding=finding,
                    blok=name,
                )
        declaration = declaration.model_copy(update={"reported": frozenset(reported)})
        replaced = [
            other
            for other, existing in self.registered_bloks.items()
            if default
            and other != name
            and existing.default
            and existing.context == context
        ]
        for other in replaced:
            # At most one default per context: the newly declared one replaces it.
            self.registered_bloks[other] = self.registered_bloks[other].model_copy(
                update={"default": False}
            )
        self.registered_bloks[name] = declaration

    def register_asset(
        self,
        name: str,
        path: str | Path | None = None,
        kind: AssetKind | None = None,
        *,
        source: str | None = None,
        ref: str | None = None,
    ) -> None:
        """Register a file the app uses, under the name its bloks call it by.

        A blok names it as ``@self.assets.<name>``: a scene blok's model, an image
        in a panel. The file is either one the app brings (``path``) or one it only
        says where to fetch (``source`` with its ``ref``).

        A file the app brings is uploaded when the app runs, if the server does not
        hold it yet. A file it names a source for is never fetched by the app: an
        admin of the organization adds it from their client, which fetches it from
        the source. Either way the server checks the bytes against the hash and the
        rules of the kind.

        Args:
            name: What bloks call the asset. An identifier, unique within the app.
            path: The file, relative to the app's directory (:attr:`root`). It is
                read now, and again when it is uploaded.
            kind: What the file is. Told from the extension when left out
                (``.glb`` is a 3D model; ``.png``, ``.jpg``, ``.jpeg`` and
                ``.webp`` are images).
            source: The https URL the file is fetched from, instead of ``path``.
            ref: The file at ``source``: ``"sha256:"`` and the hex SHA-256 of its
                exact bytes. Required with ``source``, since nothing is fetched
                here to compute it.

        Raises:
            ValueError: If the name is not an identifier or is taken by another
                file, not exactly one of ``path`` and ``source`` is given, the
                path is absolute or leaves the app's directory, the file is not
                of its kind, or the source or its reference is malformed.
            FileNotFoundError: If there is no file at ``path``.
        """
        if not name.isidentifier():
            raise ValueError(
                f"An asset's name is an identifier, as it is written in "
                f"'@self.assets.<name>': {name!r} is not one"
            )
        if (path is None) == (source is None):
            raise ValueError(
                f"The asset '{name}' is a file the app brings (a path) or one it "
                f"says where to fetch (source= with ref=): give exactly one"
            )
        if source is not None:
            self._record_asset(_sourced_asset(name, source, ref, kind))
            return
        assert path is not None, "exactly one of path and source is given"
        if ref is not None:
            raise ValueError(
                f"The asset '{name}': a ref pins a file fetched from a source; "
                f"the reference of a file the app brings is computed from it"
            )
        relative = Path(path)
        root = (self.root or Path.cwd()).resolve()
        file = (root / relative).resolve()
        if relative.is_absolute() or not file.is_relative_to(root):
            raise ValueError(
                f"The asset '{name}' must be a file inside the app's directory "
                f"({root}), given relative to it: {str(path)!r} is not"
            )
        data = file.read_bytes()
        self._record_asset(
            AssetDeclaration(
                name=name,
                kind=_asset_kind(file, data, kind),
                ref="sha256:" + hashlib.sha256(data).hexdigest(),
                size=len(data),
                path=file,
            )
        )

    def _record_asset(self, declaration: "AssetDeclaration") -> None:
        """Keep a declaration under its name; the same one again is no change."""
        existing = self.registered_assets.get(declaration.name)
        if existing == declaration:
            return
        if existing is not None:
            raise ValueError(
                f"The asset '{declaration.name}' is already registered as {existing.where}"
            )
        self._refuse_if_frozen(f"the asset '{declaration.name}'")
        self.registered_assets[declaration.name] = declaration

    def get_inspected_assets(self) -> list[InspectedAsset]:
        """The registered assets with where each is, relative to the app's directory, in name order."""
        root = (self.root or Path.cwd()).resolve()
        inspected: list[InspectedAsset] = []
        for _, asset in sorted(self.registered_assets.items()):
            said: dict[str, Any] = asset.as_input().model_dump(exclude_none=True)
            if asset.path is not None:
                said["path"] = Path(os.path.relpath(asset.path, root)).as_posix()
            inspected.append(InspectedAsset(**said))
        return inspected

    def get_declared_assets(self) -> tuple[AssetInput, ...]:
        """The registered assets as the server is told of them, in name order."""
        return tuple(
            asset.as_input() for _, asset in sorted(self.registered_assets.items())
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
        # A catalog only adds to the base: redefining one of its components is refused here.
        catalog_chain(catalog)

        existing = self.declared_catalogs.get(name)
        if existing is not None and existing != catalog:
            raise ValueError(f"The catalog {name!r} is already declared, differently.")
        self.declared_catalogs[name] = catalog

    def definition_view(self, names: Sequence[str]) -> CatalogView:
        """What a definition naming the catalogs ``names`` is checked against on this side.

        The base catalog, and those of ``names`` this app declared: what the server resolves
        a definition against, so far as the app can know it. A name it did not declare is a
        module only the server holds, which leaves an unknown operation unknowable, so
        nothing is said about one then. Base operations are fully known either way.
        """
        declared = [
            self.declared_catalogs[name]
            for name in names
            if name in self.declared_catalogs
        ]
        complete = all(
            name in self.declared_catalogs
            or name in (BASE_CATALOG_NAME, BASE_CATALOG_ID)
            for name in names
        )
        return resolve_catalogs(declared, authoritative=complete)

    def _warn_definition_findings(self) -> None:
        """Warn what this app's actions call that their catalogs do not provide.

        Warnings only, as on the server: a UI may roll an operation out before or after an
        agent uses it, and the client's knowledge of a module is partial. Each points at
        the action's own line.
        """
        for interface, implementation in self.implementations.items():
            definition = implementation.definition
            at = _function_origin(self.declared_implementations[interface].function)
            try:
                findings = validate_definition_against_catalog(
                    definition,
                    self.definition_view(definition.catalogs or ()),
                    implementation.optimistics,
                    strict=False,
                )
            except ValueError as error:
                # Two catalogs defining one operation differently: the view itself cannot be built.
                findings = [
                    Diagnostic(
                        level=WARNING, code="catalog_conflict", message=str(error)
                    )
                ]
            for finding in findings:
                message = f"Action {interface!r}: {finding.message} [{finding.code}]"
                warn_at(
                    message,
                    at,
                    finding=Finding(
                        code=finding.code, level="warning", message=finding.message
                    ),
                    blok=interface,
                )
            for port, finding in widget_tree_findings(definition, self.blok_view(None)):
                message = f"Action {interface!r}: widget of port {port!r}: {finding.message} [{finding.code}]"
                warn_at(
                    message,
                    at,
                    finding=Finding(
                        code=finding.code, level="warning", message=finding.message
                    ),
                    blok=interface,
                )

    def blok_view(self, catalog: str | None) -> BlokView:
        """What a blok written for ``catalog`` is drawn with.

        The base catalog when it names none. Else the catalog this app declared under that
        name (:meth:`declare_ui_catalog`), or the one known here under that id (one that
        ships with the package, one tooling registered), each on top of what it extends. A
        catalog nobody knows here leaves what it adds unknowable: the view is the base's, and
        no component is reported unknown.
        """
        if catalog is None:
            return default_view()
        declared = self.declared_catalogs.get(catalog)
        if declared is not None:
            return blok_view(declared)
        if known_catalog(catalog) is not None:
            return blok_view(catalog)
        return blok_view(complete=False)

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
        :func:`~arkitekt_spec.declare.definition.checks.check_agent_input` then runs the checks
        that need the whole declaration (search queries, state references, blok components), so
        this is the single validated retrieval point for everything the agent
        registers.

        Args:
            name: What identifies the agent. The backend falls back to the
                client id when it is omitted.
            description: What the agent is, in a sentence. Omitting it leaves
                whatever the agent already has, rather than clearing it.
        """
        from arkitekt_spec.declare.definition.checks import check_agent_input

        # Passed only when there is one, so that an app declaring no description
        # (or no assets) leaves the field *unset* rather than sending an explicit
        # null: the declaration is dumped with ``exclude_unset``, so this is what
        # keeps such an agent's wire bytes (and its definition hash) exactly what
        # they were before the field existed.
        optional: dict[str, Any] = {}
        if description is not None:
            optional["description"] = description
        if self.registered_assets:
            optional["assets"] = self.get_declared_assets()
        # What the port bloks of its actions read of the agent is declared by now.
        check_port_blok_reads(self)
        return check_agent_input(
            ImplementAgentInput(
                name=name,
                implementations=tuple(self.get_implementations()),
                states=tuple(self.states.values()),
                locks=tuple(self.get_locks()),
                bloks=tuple(self.get_declared_bloks().values()),
                **optional,
            )
        )

    def to_registered_agent_input(
        self,
        name: str | None = None,
        description: str | None = None,
    ) -> ImplementAgentInput:
        """The agent input as it is registered with a server (``Register``).

        The same declaration as :meth:`to_implement_agent_input`, checked the same way, with each
        blok as it is sent (:func:`~arkitekt_spec.declare.blok.registry.as_registered`): one
        declared as its source goes without the ``self`` dependency built here, which the server
        works out from the source.
        """
        checked = self.to_implement_agent_input(name=name, description=description)
        # A copy, not a second validation: the whole declaration was checked with the
        # dependencies in place, and the rules that read them have nothing to say of what is sent.
        return checked.model_copy(
            update={"bloks": tuple(as_registered(blok) for blok in checked.bloks or ())}
        )

    # ------------------------------------------------------------------ #
    # Snapshots                                                          #
    # ------------------------------------------------------------------ #

    def snapshot(self, clients: Mapping[str, Any] | None = None) -> "AppRegistry":
        """Take what one run of this registry serves: validated, bound and frozen -- as a copy.

        This registry is the *declaration* and is never changed by running it. Each
        run takes its own snapshot, so one declaration can be run any number of
        times, concurrently too, without the runs seeing each other's clients.

        Args:
            clients: The run's clients, by name; every structure is bound to the
                clients its expander asks for in the snapshot. Without them
                nothing is bound yet: a runtime whose clients need the snapshot to
                be built (rekuest's agent does) binds afterwards, with
                ``snapshot.structure_registry.bind(clients)`` -- the snapshot owns
                its structure maps, so that never reaches the declaration.

        Returns:
            The snapshot: a frozen registry whose state schemas point at its own
            structure registry, and which uses the same clients. A runtime builds
            its actors against that structure registry.

        Raises:
            StructureRegistryError: If a port names a structure nothing here holds.
            StructureClientError: If a structure's client is missing.
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
            registered_bloks=dict(self.registered_bloks),
            registered_assets=dict(self.registered_assets),
            root=self.root,
            declared_catalogs=dict(self.declared_catalogs),
            hooks_registry=HooksRegistry.model_construct(
                background_worker=dict(self.hooks_registry.background_worker),
                startup_hooks=dict(self.hooks_registry.startup_hooks),
                shutdown_hooks=dict(self.hooks_registry.shutdown_hooks),
            ),
            structure_registry=structures,
            clients=dict(self.clients),
            provider=self.provider,
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
        naming an identifier that *this* registry cannot resolve, which would
        otherwise surface as a failed expansion, mid-assignment, long after the
        cause.

        Called when the app is assembled, before it connects. Nothing here needs a
        connection.

        What an action calls is checked against its catalogs here too, once all of them
        are declared. That only warns, as a :class:`CatalogWarning`.

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

        def check(port: ArgPortInput | ReturnPortInput, where: str) -> None:
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

        if problems:
            raise StructureRegistryError(
                "This app is not valid:\n"
                + "\n".join(sorted(problems))
                + "\nA structure comes with the client that owns it, so the usual "
                "cause is a registry filled by a different app. Expanding one of "
                "these would fail mid-assignment instead."
            )

        self._warn_definition_findings()

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
        persist: Literal["synchronous"] | float | None = None,
    ) -> type[T]: ...

    @overload
    def state(
        self,
        /,
        *,
        name: str | None = None,
        required_locks: list[str] | None = None,
        persist: Literal["synchronous"] | float | None = None,
    ) -> Callable[[type[T]], type[T]]: ...

    @dataclass_transform(field_specifiers=(model_field,))
    def state(
        self,
        *args: type[T],
        name: str | None = None,
        required_locks: list[str] | None = None,
        persist: Literal["synchronous"] | float | None = None,
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
            persist=persist,
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

    def lifecycle(
        self,
        func: T | None = None,
        /,
        *,
        name: str | None = None,
    ) -> T | Callable[[T], T]:
        """Register a lifecycle hook: a startup and its shutdown in one async generator."""
        from arkitekt_spec.declare.agents.hooks.declare import declare_lifecycle

        return declare_lifecycle(  # type: ignore[return-value]
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
        :meth:`~arkitekt_spec.declare.structures.registry.StructureRegistry.context`: the class
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

    def require_app_context(self, value: object, *, whose: str) -> None:
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
        check_app_context(self.app_context_class, value, whose=whose)

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
        :meth:`~arkitekt_spec.declare.structures.registry.StructureRegistry.model`: the class
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
            identifier: What to call it. Defaults to the class name in the app's
                namespace for a class the app defines (``@myapp/blob``), else in its
                module (``numpy.ndarray`` -> ``@numpy/ndarray``).
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
        is_test_for: list[TestTargetInput] | None = None,
        validators: dict[str, list[ValidatorInput]] | None = None,
        optimistics: list[OptimisticCoercible] | None = None,
        in_process: bool = False,
        locks: list[str] | None = None,
        concurrency: Literal["parallel", "serial"] = "serial",
        policy: DisconnectPolicy = KEEP,
        version: str | None = None,
        catalogs: list[str] | None = None,
        protocols: list[str] | None = None,
        effects: Effects | None = None,
        execution: Execution = Execution.PLAIN,
        logo: "str | ComponentNodeInput | None" = None,
        bloks: "ActionBloks | None" = None,
        dynamic_asks: bool = False,
        dynamic_calls: bool = False,
        dynamic_informs: bool = False,
        estimated_runtime: str | None = None,
        estimated_runtime_ui: str | None = None,
        declared_at: DeclaredAt | None = None,
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
            is_test_for: Actions this one tests.
            validators: Input validation rules per argument.
            optimistics: Optimistic outputs.
            in_process: Run in the event loop instead of a worker thread.
            locks: Locks held while an assignment runs.
            concurrency: Whether assignments may run concurrently.
            policy: What happens to a running assignment when its caller
                disconnects.
            version: Version of the definition.
            catalogs: Catalogs the action is listed in.
            protocols: Where the action means to hook in (``on_scene_annotation``):
                hints a caller filters on, never a contract, and not part of the hash.
            effects: What running it again would do to the world. Informational;
                None takes the app's default.
            execution: ``Execution.WORKFLOW`` to call other actions and be resumed
                after a crash (see :meth:`register_workflow`).
            logo: The logo that draws the action: the name of an action logo blok
                this app declares (a ``<Blok kind="action" context="logo">`` registered with
                ``register_blok``), or a tree of its own (``bsx(...)``).
                Without one, a client shows the app's default logo.
            bloks: The action's own bloks, one per kind and context, each a ``<Blok>`` (or an
                ``ActionBlok`` for a demo and a description; one needs no list):
                ``kind="action"`` for how a client shows a run of it, ``kind="assign"`` for
                starting one (``<args />`` for the argument form, ``assign.start()`` to start
                the run with the agents the blok was shown). They name devices only by the function's
                dependency parameters, are checked now, and are not part of the hash.
            dynamic_asks: Whether a task may also ask through a blok made at ask time
                (``task.dynamic_ask``), not only the interactions protocols it is handed.
            dynamic_calls: Whether a task may also call actions it found at run time
                (``task.call``), not only its dependencies. A workflow only.
            dynamic_informs: Whether a task may show a blok made at inform time
                (``task.inform`` by source or id), not only the app's own bloks.
            estimated_runtime: A formula for how long it runs, in seconds, that the
                action query filters and sorts by. Parsed here, never run, and refused
                unless the server can compute it.
            estimated_runtime_ui: A richer formula for the form only, which needs
                ``estimated_runtime``: any pure function, and the form's values too.
            declared_at: The app's line that declared it, where its blok warnings point.
                Defaults to the caller's.

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
            is_test_for=is_test_for,
            validators=validators,
            optimistics=optimistics,
            in_process=in_process,
            locks=locks,
            concurrency=concurrency,
            policy=policy,
            version=version,
            catalogs=catalogs,
            protocols=protocols,
            effects=effects,
            execution=execution,
            logo=logo,
            bloks=bloks,
            dynamic_asks=dynamic_asks,
            dynamic_calls=dynamic_calls,
            dynamic_informs=dynamic_informs,
            estimated_runtime=estimated_runtime,
            estimated_runtime_ui=estimated_runtime_ui,
            declared_at=declared_at,
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


__all__ = [
    "DEPENDENCY_CALL_SCOPE",
    "DYNAMIC_ASK_SCOPE",
    "DYNAMIC_CALL_SCOPE",
    "DYNAMIC_INFORM_SCOPE",
    "AppRegistry",
    "check_app_context",
]
