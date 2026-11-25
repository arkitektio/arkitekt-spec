"""A client declares itself: ``@client(...)`` on its class.

The class is what an action, a hook or a structure's expander asks for
(``mikro: Mikro``); the declaration says what building one requires of a
deployment, read off its default binder::

    @client(schema=..., turms=..., image="jhnnsrs/mikro:15")
    class Mikro(Composition, MikroApi):
        @classmethod
        def from_fakts(
            cls,
            mikro: Annotated[Alias, Require("live.arkitekt.mikro")],
            s3: Annotated[Alias, Require("live.arkitekt.s3")],
            tokens: TokenLoader,
        ) -> Self: ...

Nothing is registered: an app finds the client through the annotations of what
it offers, or names it (``App(clients=[Mikro])``). Building one is a run's job --
with the binder by default, or a run is handed an instance.
"""

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generic, Self, TypeVar, get_type_hints

import inflection

from arkitekt_spec.declare.blok.origin import DeclaredAt, declared_at
from arkitekt_spec.declare.injections import (
    BinderDefinitionError,
    Injection,
    needs_fakts,
    read_injections,
    requirements_of,
)
from arkitekt_spec.declare.markers import CLIENT, marker_of, stamp
from arkitekt_spec.declare.wiring import Require
from arkitekt_spec.manifest import Requirement

if TYPE_CHECKING:
    from arkitekt_spec.declare.structures.declare import StructureDeclaration

C = TypeVar("C")

DEFAULT_BINDER = "from_fakts"
"""The classmethod a client is built with by default, when its class has one."""


class ClientDefinitionError(BinderDefinitionError):
    """A client's declaration does not say what a run needs to know."""


class ClientDeclaration(Generic[C]):
    """What ``@client`` says about a client class.

    Attributes:
        name: What a run keeps the client under; the class name in snake case.
        cls: The client class.
        binder: The classmethod a run builds one with, or ``None``: then it can
            only be handed in.
        image: The image hosting the server the client talks to, for test hubs.
        structures: The structures whose expanders ask for this client, appended
            by ``@structure``. An app using the client takes all of them.
    """

    def __init__(
        self,
        cls: type[C],
        *,
        name: str,
        binder: str | None,
        requires: Mapping[str, Require] | None,
        schema: str | Path | None,
        turms: str | Path | None,
        image: str | None,
        declared_at: DeclaredAt | None,
    ) -> None:
        self.cls: type[C] = cls
        self.name = name
        self.binder = binder
        self.requires = dict(requires) if requires is not None else None
        self.image = image
        self.declared_at = declared_at
        self.structures: list[StructureDeclaration] = []
        self._schema = schema
        self._turms = turms
        self._injections: list[Injection] | None = None

    def __repr__(self) -> str:
        keys = ", ".join(r.key for r in self.get_requirements()) or "nothing"
        return f"Client({self.name!r}, requires {keys})"

    @property
    def binder_function(self) -> Callable[..., Any] | None:
        """The binder, bound to the class: called with what its parameters ask for."""
        return getattr(self.cls, self.binder) if self.binder is not None else None

    @property
    def injections(self) -> list[Injection]:
        """What the binder asks for, read on first use.

        Not at decoration: inside ``@client`` the class's name is not bound in its
        module yet, so a ``-> "Mikro"`` annotation would not resolve.

        Raises:
            ClientDefinitionError: If the binder's signature is not one a run can fill.
        """
        if self._injections is None:
            function = self.binder_function
            if function is None:
                self._injections = []
            else:
                self._check_returns(function)
                try:
                    self._injections = read_injections(
                        f"{self.cls.__qualname__}.{self.binder}", function
                    )
                except BinderDefinitionError as e:
                    raise ClientDefinitionError(str(e)) from None
        return self._injections

    def _check_returns(self, function: Callable[..., Any]) -> None:
        returns = get_type_hints(function).get("return")
        if returns is None or returns is Self:
            return
        if not (isinstance(returns, type) and issubclass(returns, self.cls)):
            raise ClientDefinitionError(
                f"{self.cls.__qualname__}.{self.binder} returns {returns!r}: a binder "
                f"builds a {self.cls.__qualname__} (annotate it -> Self)."
            )

    def get_requirements(self) -> list[Requirement]:
        """What building the client requires of a deployment: static, read off the binder."""
        if self.requires is not None:
            return [r.to_requirement(key) for key, r in self.requires.items()]
        return requirements_of(self.injections)

    def get_scopes(self) -> list[str]:
        """What an app using the client must be allowed: the class's ``__scopes__``."""
        scopes = getattr(self.cls, "__scopes__", ())
        if not isinstance(scopes, (tuple, list, frozenset)) or not all(
            isinstance(scope, str) for scope in scopes
        ):
            raise ClientDefinitionError(
                f"{self.name}: {self.cls.__name__}.__scopes__ is not a collection of scope names"
            )
        return sorted(scopes)

    @property
    def needs_fakts(self) -> bool:
        """Whether building it with its binder needs a run's fakts."""
        return needs_fakts(self.injections)

    @property
    def takes_registry(self) -> bool:
        """Whether its binder needs the run's registry: such a client cannot be handed in."""
        return any(i.kind == "registry" for i in self.injections)

    def get_graphql_schema(self) -> str | None:
        """The service's GraphQL schema as SDL, for code generation and blok checks."""
        return Path(self._schema).read_text(encoding="utf-8") if self._schema else None

    def get_turms_project(self) -> dict[str, Any] | None:
        """The turms project that generates the client."""
        return (
            json.loads(Path(self._turms).read_text(encoding="utf-8"))
            if self._turms
            else None
        )


def client(
    *,
    name: str | None = None,
    binder: str | None = None,
    requires: Mapping[str, Require] | None = None,
    schema: str | Path | None = None,
    turms: str | Path | None = None,
    image: str | None = None,
) -> Callable[[type[C]], type[C]]:
    """Declare a client class: what it is called and what building one requires.

    Args:
        name: What a run keeps it under. Defaults to the class name in snake case.
        binder: The classmethod that builds it. Defaults to ``from_fakts`` when the
            class has one.
        requires: The requirements of a client without a binder (one only ever
            handed in); a binder states its own.
        schema: Path to the service's GraphQL schema.
        turms: Path to the turms project generating the client.
        image: The image hosting the server it talks to, e.g. ``"jhnnsrs/mikro:15"``.

    Returns:
        A class decorator returning the class, marked.

    Raises:
        ClientDefinitionError: If there is neither a binder nor ``requires``, both,
            or the named binder does not exist.
    """
    at = declared_at()

    def decorate(cls: type[C]) -> type[C]:
        chosen = binder
        if chosen is None and requires is None and hasattr(cls, DEFAULT_BINDER):
            chosen = DEFAULT_BINDER
        if chosen is not None and not callable(getattr(cls, chosen, None)):
            raise ClientDefinitionError(
                f"{cls.__qualname__} names the binder '{chosen}', which it does not have."
            )
        if chosen is None and requires is None:
            raise ClientDefinitionError(
                f"{cls.__qualname__} has no binder ({DEFAULT_BINDER}) and states no "
                "requires=: a run could neither build it nor say what it needs."
            )
        if chosen is not None and requires is not None:
            raise ClientDefinitionError(
                f"{cls.__qualname__} has a binder and requires=: the binder's "
                "parameters are its requirements, so state them once."
            )
        declaration = ClientDeclaration(
            cls,
            name=name or inflection.underscore(cls.__name__),
            binder=chosen,
            requires=requires,
            schema=schema,
            turms=turms,
            image=image,
            declared_at=at,
        )
        stamp(cls, CLIENT, declaration, same=lambda old, new: old.name == new.name)
        return cls

    return decorate


def client_of(cls: object) -> ClientDeclaration[Any] | None:
    """The client declaration ``cls`` itself carries, or ``None``."""
    return marker_of(cls, CLIENT)


__all__ = [
    "DEFAULT_BINDER",
    "ClientDeclaration",
    "ClientDefinitionError",
    "client",
    "client_of",
]
