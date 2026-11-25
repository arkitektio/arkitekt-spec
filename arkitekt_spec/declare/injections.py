"""What a binder's parameters ask a run for, read off their annotations.

A client's binder and a provider are plain functions: each parameter says what
fills it by its annotation alone. Reading that is declaration; filling it (through
fakts) is the runtime's.
"""

import inspect
from collections.abc import Callable
from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

from arkitekt_spec.declare.markers import CLIENT, marker_of
from arkitekt_spec.declare.wiring import (
    Alias,
    Own,
    Require,
    TokenLoader,
    is_fakts_client,
)
from arkitekt_spec.manifest import AppManifest, Requirement

InjectionKind = Literal[
    "alias", "own", "tokens", "manifest", "fakts", "registry", "client"
]


class BinderDefinitionError(TypeError):
    """A binder's signature does not say what a run needs to know."""


class Injection:
    """One parameter of a binder, and what fills it."""

    __slots__ = ("cls", "kind", "name", "require")

    def __init__(
        self,
        name: str,
        kind: InjectionKind,
        require: Require | None = None,
        cls: type | None = None,
    ) -> None:
        self.name = name
        self.kind: InjectionKind = kind
        self.require = require
        self.cls = cls

    def __repr__(self) -> str:
        return f"Injection({self.name!r}, {self.kind!r})"


def _wants_alias(inner: object) -> tuple[bool, bool]:
    """Whether an annotation's inner type is an ``Alias``, and whether it is optional."""
    if inner is Alias:
        return True, False
    # ``Optional[Alias]`` is a ``typing.Union``, ``Alias | None`` a ``types.UnionType``.
    if get_origin(inner) in (Union, UnionType):
        args = set(get_args(inner))
        if Alias in args and args <= {Alias, type(None)}:
            return True, type(None) in args
    return False, False


def injection_for(
    owner: str, name: str, annotation: object, *, clients: bool = False
) -> Injection:
    """What a parameter wants, from its annotation alone.

    Args:
        owner: The binder's name, for messages.
        name: The parameter's name.
        annotation: Its annotation, ``Annotated`` extras kept.
        clients: Whether a ``@client`` class may be asked for. Only a provider is
            built after the clients exist; a client's binder never takes another.

    Raises:
        BinderDefinitionError: If the annotation names nothing a run supplies.
    """
    from arkitekt_spec.declare.agents.types import unwrap_injectable
    from arkitekt_spec.declare.app import AppRegistry

    if get_origin(annotation) is not None and get_args(annotation):
        inner, *extras = get_args(annotation)
        is_alias, allows_none = _wants_alias(inner)
        if is_alias:
            if any(isinstance(marker, Own) for marker in extras):
                return Injection(name, "own")
            markers = [marker for marker in extras if isinstance(marker, Require)]
            if not markers:
                raise BinderDefinitionError(
                    f"'{owner}' asks for an Alias as '{name}' but does not say which "
                    f'service fills it: Annotated[Alias, Require("live.arkitekt.{name}")].'
                )
            if len(markers) > 1:
                raise BinderDefinitionError(
                    f"'{owner}' marks '{name}' with {len(markers)} Requires; a "
                    "parameter is one requirement."
                )
            require = markers[0]
            if require.optional and not allows_none:
                raise BinderDefinitionError(
                    f"'{owner}' marks '{name}' optional: say so in the type, Optional[Alias]."
                )
            if allows_none and not require.optional:
                raise BinderDefinitionError(
                    f"'{owner}' types '{name}' as Optional[Alias] but requires it: drop "
                    "the Optional or mark the Require optional=True."
                )
            return Injection(name, "alias", require)

    if annotation is Alias:
        raise BinderDefinitionError(
            f"'{owner}' asks for a bare Alias as '{name}'. Say which service: "
            f'Annotated[Alias, Require("live.arkitekt.{name}")].'
        )
    if annotation is TokenLoader:
        return Injection(name, "tokens")
    if isinstance(annotation, type) and issubclass(annotation, AppManifest):
        return Injection(name, "manifest")
    if is_fakts_client(annotation):
        return Injection(name, "fakts")
    if isinstance(annotation, type) and issubclass(annotation, AppRegistry):
        return Injection(name, "registry")
    if clients:
        cls = unwrap_injectable(annotation)
        if marker_of(cls, CLIENT) is not None:
            assert isinstance(cls, type)
            return Injection(name, "client", cls=cls)

    raise BinderDefinitionError(
        f"'{owner}' asks for '{name}: {annotation!r}', which a run cannot supply. A "
        "parameter is one of: Annotated[Alias, Require(...)], TokenLoader, AppManifest, "
        "AppRegistry, or Fakts when it genuinely needs the whole client"
        + ("; a provider may also ask for a @client class." if clients else ".")
    )


def read_injections(
    owner: str, function: Callable[..., Any], *, clients: bool = False
) -> list[Injection]:
    """What each parameter of ``function`` wants; a bound classmethod skips ``cls``.

    Raises:
        BinderDefinitionError: If a parameter is unannotated or not injectable.
    """
    hints = get_type_hints(function, include_extras=True)
    injections: list[Injection] = []
    for parameter in inspect.signature(function).parameters.values():
        if parameter.name not in hints:
            raise BinderDefinitionError(
                f"'{owner}' has an unannotated parameter '{parameter.name}': a "
                "parameter says what it wants by its annotation."
            )
        injections.append(
            injection_for(owner, parameter.name, hints[parameter.name], clients=clients)
        )
    return injections


def requirements_of(injections: list[Injection]) -> list[Requirement]:
    """The requirements the ``alias`` parameters state, keyed by parameter name."""
    return [
        injection.require.to_requirement(injection.name)
        for injection in injections
        if injection.kind == "alias" and injection.require is not None
    ]


def needs_fakts(injections: list[Injection]) -> bool:
    """Whether filling these needs a run's fakts: anything but the registry and clients."""
    return any(i.kind not in ("registry", "client") for i in injections)


__all__ = [
    "BinderDefinitionError",
    "Injection",
    "InjectionKind",
    "injection_for",
    "needs_fakts",
    "read_injections",
    "requirements_of",
]
