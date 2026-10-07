"""A service: one function whose parameters are its requirements.

A client package (mikro, fluss, ...) declares one on the registry it ships, beside
the structures the client expands, and exports it
(``from mikro import mikro_service``)::

    registry = AppRegistry()

    @registry.service(schema=..., turms=...)
    def mikro(
        mikro: Annotated[Alias, Require("live.arkitekt.mikro")],
        s3:    Annotated[Alias, Require("live.arkitekt.s3")],
        tokens: TokenLoader,
    ) -> Mikro:
        ...

    @registry.structure("@mikro/arraydataset")
    async def expand_array_dataset(id: str, mikro: Mikro) -> ArrayDataset:
        return await mikro.aget_array_dataset(id)

Everything a run needs is read off the signature: the service's **name** is the
function's, **what it returns** is the client, and its **requirements** are its
:class:`~fakts.Alias` parameters -- parameter name as the key, the
:class:`~fakts.Require` marker for the rest. The return annotation is also what
ties the structures to the service: an expander (or an action) asking for a
``mikro: Mikro`` is handed the client of the service that returns a ``Mikro``.

Declaring registers nothing process-wide. An app takes a service in with
``App(services=[mikro_service])``, which merges the registry it was declared on,
and a run builds clients for exactly the services its registry holds.
"""

import inspect
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from types import UnionType
from typing import (
    TYPE_CHECKING,
    Any,
    Generic,
    TypeVar,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

from arkitekt_spec.declare.wiring import Alias, Own, Require, TokenLoader, is_fakts_client
from arkitekt_spec.manifest import AppManifest, Requirement

if TYPE_CHECKING:
    from arkitekt_spec.declare.app import AppRegistry
    from arkitekt_spec.declare.structures.registry import StructureRegistry

C = TypeVar("C")
"""What a service's function returns: its client."""


class ServiceDefinitionError(TypeError):
    """A service's signature does not say what a run needs to know."""


#: What a parameter asks to be handed, worked out once at decoration time.
_ALIAS = "alias"
_OWN = "own"
_TOKENS = "tokens"
_MANIFEST = "manifest"
_FAKTS = "fakts"
_REGISTRY = "registry"
_CLIENT = "client"
"""A built client, by class: only a provider takes these (it is built after them)."""


class _Injection:
    """One parameter of a service or provider function, and what fills it."""

    __slots__ = ("cls", "kind", "name", "require")

    def __init__(
        self,
        name: str,
        kind: str,
        require: Require | None = None,
        cls: type | None = None,
    ) -> None:
        self.name = name
        self.kind = kind
        self.require = require
        self.cls = cls


def _wants_alias(inner: Any) -> tuple[bool, bool]:
    """Whether an annotation's inner type is an ``Alias``, and whether it is optional.

    Args:
        inner: The type inside ``Annotated[...]``.

    Returns:
        ``(is_alias, allows_none)``.
    """
    if inner is Alias:
        return True, False
    # ``Optional[Alias]`` and ``Alias | None`` are different objects: the first a
    # ``typing.Union``, the second a ``types.UnionType``. Both mean the same here.
    if get_origin(inner) in (Union, UnionType):
        args = set(get_args(inner))
        if Alias in args and args <= {Alias, type(None)}:
            return True, type(None) in args
    return False, False


def _injection_for(
    function_name: str,
    name: str,
    annotation: Any,
    *,
    clients_of: "StructureRegistry | None" = None,
) -> _Injection:
    """Work out what a parameter wants, from its annotation alone.

    Args:
        function_name: The service's name, for error messages.
        name: The parameter's name.
        annotation: Its annotation, with ``Annotated`` extras kept.
        clients_of: The registry whose services' clients may be asked for by
            annotation. Only a provider is built after the clients exist, so
            only it passes one; a service never takes another client.

    Returns:
        What fills the parameter.

    Raises:
        ServiceDefinitionError: If the annotation names nothing injectable, or a
            ``Fakt`` carries no :class:`~fakts.Require`.
    """
    from arkitekt_spec.declare.app import AppRegistry

    if get_origin(annotation) is not None and get_args(annotation):
        inner, *extras = get_args(annotation)
        is_alias, allows_none = _wants_alias(inner)
        if is_alias:
            if any(isinstance(marker, Own) for marker in extras):
                return _Injection(name, _OWN)
            markers = [marker for marker in extras if isinstance(marker, Require)]
            if not markers:
                raise ServiceDefinitionError(
                    f"'{function_name}' asks for an Alias as '{name}' but does not say "
                    f"which service fills it. Mark it: "
                    f'Annotated[Alias, Require("live.arkitekt.{name}")].'
                )
            if len(markers) > 1:
                raise ServiceDefinitionError(
                    f"'{function_name}' marks '{name}' with {len(markers)} Requires; "
                    "a parameter is one requirement."
                )
            require = markers[0]
            if require.optional and not allows_none:
                raise ServiceDefinitionError(
                    f"'{function_name}' marks '{name}' optional, so a run may not be able "
                    f"to resolve it. Say so in the type: Optional[Alias]."
                )
            if allows_none and not require.optional:
                raise ServiceDefinitionError(
                    f"'{function_name}' types '{name}' as Optional[Alias] but requires it. "
                    "Either drop the Optional or mark the Require optional=True."
                )
            return _Injection(name, _ALIAS, require)

    if annotation is Alias:
        raise ServiceDefinitionError(
            f"'{function_name}' asks for a bare Alias as '{name}'. An Alias is where a "
            f"required service is, so say which: "
            f'Annotated[Alias, Require("live.arkitekt.{name}")].'
        )
    if annotation is TokenLoader:
        return _Injection(name, _TOKENS)
    if isinstance(annotation, type) and issubclass(annotation, AppManifest):
        return _Injection(name, _MANIFEST)
    if is_fakts_client(annotation):
        return _Injection(name, _FAKTS)
    if isinstance(annotation, type) and issubclass(annotation, AppRegistry):
        return _Injection(name, _REGISTRY)
    if clients_of is not None and clients_of.is_client(annotation):
        from arkitekt_spec.declare.agents.types import unwrap_injectable

        cls = unwrap_injectable(annotation)
        assert isinstance(cls, type)
        return _Injection(name, _CLIENT, cls=cls)

    raise ServiceDefinitionError(
        f"'{function_name}' asks for '{name}: {annotation!r}', which a run cannot "
        "supply. A service parameter is one of: Annotated[Alias, Require(...)] for a "
        "requirement, TokenLoader for authentication, AppManifest for what the app says "
        "it is, AppRegistry for the run's registry, "
        "or Fakts when a service genuinely needs the whole client"
        + (
            "; a provider may also ask for a client a service declared here returns."
            if clients_of is not None
            else "."
        )
    )


def _read_injections(
    function_name: str,
    function: Callable[..., Any],
    *,
    clients_of: "StructureRegistry | None" = None,
) -> tuple[type, list[_Injection]]:
    """Read what ``function`` returns and what each parameter wants.

    Shared by services and providers: the return annotation must be a class and
    every parameter must be annotated with something a run can supply.

    Raises:
        ServiceDefinitionError: If the signature does not say what a run needs.
    """
    hints = get_type_hints(function, include_extras=True)

    returns = hints.get("return")
    if returns is None:
        raise ServiceDefinitionError(
            f"'{function_name}' does not say what it returns. Annotate it (`-> Mikro`): "
            "that is what a parameter annotated with it is handed."
        )
    if not isinstance(returns, type):
        raise ServiceDefinitionError(
            f"'{function_name}' returns {returns!r}, which is not a class."
        )

    injections: list[_Injection] = []
    for parameter in inspect.signature(function).parameters.values():
        if parameter.name not in hints:
            raise ServiceDefinitionError(
                f"'{function_name}' has an unannotated parameter '{parameter.name}'. A "
                "parameter says what it wants by its annotation."
            )
        injections.append(
            _injection_for(
                function_name, parameter.name, hints[parameter.name], clients_of=clients_of
            )
        )
    return returns, injections


class Service(Generic[C]):
    """A declared service: what it requires, what it returns, and how to build it.

    Made by :meth:`~rekuest.app.AppRegistry.service`; a client package never
    constructs one directly. An app takes it in (``App(services=[mikro_service])``)
    by merging the registry it was declared on, reads its requirements into the
    manifest, and a run calls :meth:`build`.

    Attributes:
        name: What the service is built under, e.g. ``"mikro"``.
        returns: What its function returns -- the client's class, e.g. ``Mikro``.
            A parameter annotated with it, on an action or an expander, is handed
            the built client.
        registry: The registry it was declared on: the structures and actions
            the client package brings with it.
        image: The image that hosts the server this client talks to, if the
            package says: what a deployment made for a test runs.
    """

    def __init__(
        self,
        function: Callable[..., Any],
        name: str,
        returns: type[C],
        injections: Sequence[_Injection],
        registry: "AppRegistry",
        schema: str | Path | None,
        turms: str | Path | None,
        image: str | None = None,
    ) -> None:
        self._function = function
        self._injections = list(injections)
        self._schema = schema
        self._turms = turms
        self.image = image
        self.name = name
        self.returns: type[C] = returns
        self.registry = registry

    def __repr__(self) -> str:
        keys = ", ".join(r.key for r in self.get_requirements()) or "nothing"
        return f"Service({self.name!r}, requires {keys})"

    # ------------------------------------------------------------------ #
    # What it is                                                         #
    # ------------------------------------------------------------------ #

    def get_requirements(self) -> list[Requirement]:
        """Say what this service needs from a deployment.

        Read off the signature, so it cannot disagree with what the builder
        actually resolves.

        Returns:
            The fakts requirements that go into every manifest of an app using it.
        """
        return [
            injection.require.to_requirement(injection.name)
            for injection in self._injections
            if injection.kind == _ALIAS and injection.require is not None
        ]

    @property
    def needs_fakts(self) -> bool:
        """Whether building this service needs a run's fakts.

        Only a builder that takes the registry alone does not; a requirement, a
        token loader or the whole client all resolve through fakts.
        """
        return any(injection.kind != _REGISTRY for injection in self._injections)

    def get_graphql_schema(self) -> str | None:
        """Give the service's GraphQL schema, for code generation.

        Returns:
            The schema as SDL, or ``None`` if the service has none.
        """
        return Path(self._schema).read_text() if self._schema else None

    def get_turms_project(self) -> dict[str, Any] | None:
        """Give the turms project that generates this service's client.

        Returns:
            The turms project configuration, or ``None``.
        """
        return json.loads(Path(self._turms).read_text()) if self._turms else None

    # ------------------------------------------------------------------ #
    # Building                                                           #
    # ------------------------------------------------------------------ #

    async def build(self, fakts: Any, registry: "AppRegistry") -> C:
        """Resolve this service's requirements and build its client, once.

        Each requirement is resolved here, when the run connects, and the
        resolved address is handed to the builder. It does not change again: an
        alias is a route to one specific service, and moving a live client to a
        different service is not supported -- if a deployment moves, the run
        ends and the next one resolves afresh.

        Args:
            fakts: The run's fakts, which the requirements resolve through;
                ``None`` for a run that built none.
            registry: The run's registry snapshot, for a service that serves it.

        Returns:
            The client.

        Raises:
            RuntimeError: If the service needs fakts and the run has none.
            AliasNotFoundError: If a required service cannot be resolved. An
                optional one yields ``None`` instead.
        """
        if fakts is None and self.needs_fakts:
            raise RuntimeError(
                f"'{self.name}' needs the run's fakts to resolve what it requires, "
                "but this run built none."
            )
        kwargs: dict[str, Any] = {}
        for injection in self._injections:
            if injection.kind == _REGISTRY:
                kwargs[injection.name] = registry
                continue
            assert fakts is not None, "needs_fakts was checked above"
            if injection.kind == _ALIAS:
                require = injection.require
                assert require is not None, "an alias injection carries its Require"
                if require.optional:
                    kwargs[injection.name] = await fakts.aget_alias_or_none(
                        injection.name
                    )
                else:
                    kwargs[injection.name] = await fakts.aget_alias(injection.name)
            elif injection.kind == _OWN:
                kwargs[injection.name] = await fakts.aget_self_alias()
            elif injection.kind == _MANIFEST:
                kwargs[injection.name] = fakts.manifest
            else:
                kwargs[injection.name] = fakts

        built = self._function(**kwargs)
        return await built if inspect.isawaitable(built) else built


def declare_service(
    registry: "AppRegistry",
    function: Callable[..., C],
    *,
    schema: str | Path | None = None,
    turms: str | Path | None = None,
    image: str | None = None,
) -> Service[C]:
    """Read a service off ``function``, declared on ``registry``.

    What :meth:`~rekuest.app.AppRegistry.service` does once it has the function:
    the function's name is the service's, its return annotation is the client,
    and its parameters are what a run hands it -- see the module docstring.

    Args:
        registry: The registry the service is declared on.
        function: The builder.
        schema: Path to the service's GraphQL schema, for code generation.
        turms: Path to the turms project that generates its client.
        image: The image that hosts the server this client talks to.

    Returns:
        The :class:`Service`.

    Raises:
        ServiceDefinitionError: If the signature does not say what a run needs:
            a parameter that is not injectable, an ``Alias`` without a
            ``Require``, or no return annotation.
    """
    name = function.__name__
    returns, injections = _read_injections(name, function)

    return Service(
        function=function,
        name=name,
        returns=returns,
        injections=injections,
        registry=registry,
        schema=schema,
        turms=turms,
        image=image,
    )
