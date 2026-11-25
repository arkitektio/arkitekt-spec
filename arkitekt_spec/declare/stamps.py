"""Classes that declare what they are: ``@model``, ``@state``, ``@context``, ``@declare``, ``@interactions``.

Each decorator marks the class with the options written on it and returns it; an app
that meets the class in an annotation takes it in when it is assembled, building its
ports there, against that app. ``@app.model`` and friends say the same for one app
only, and win over the mark.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, is_dataclass
from dataclasses import dataclass as make_dataclass
from typing import Literal, TypeVar, dataclass_transform, overload

from arkitekt_spec.declare.blok.origin import DeclaredAt, declared_at
from arkitekt_spec.declare.markers import (
    CONTEXT,
    INTERACTIONS,
    MODEL,
    PROTOCOL,
    STATE,
    stamp,
)
from arkitekt_spec.declare.structures.model import ensure_model_dataclass, model_field

T = TypeVar("T")


@dataclass(frozen=True)
class ModelMark:
    """``@model``: a class whose instances travel by value."""

    identifier: str | None = None
    description: str | None = None
    declared_at: DeclaredAt | None = field(default=None, compare=False)


@dataclass(frozen=True)
class StateMark:
    """``@state``: a class an agent publishes the value of."""

    name: str | None = None
    required_locks: tuple[str, ...] = ()
    persist: Literal["synchronous"] | float | None = None
    declared_at: DeclaredAt | None = field(default=None, compare=False)


@dataclass(frozen=True)
class ContextMark:
    """``@context``: a class an agent keeps one of, handed to what asks for it."""

    name: str | None = None
    locks: tuple[str, ...] = ()
    declared_at: DeclaredAt | None = field(default=None, compare=False)


@dataclass(frozen=True)
class ProtocolMark:
    """``@declare``: what an app demands of another app."""

    app: str | None = None
    auto_resolvable: bool = False
    min: int | None = None
    max: int | None = None
    version: str | None = None
    declared_at: DeclaredAt | None = field(default=None, compare=False)


@dataclass(frozen=True)
class InteractionsMark:
    """``@interactions``: a class of ``@ask`` and ``@inform`` methods."""

    declared_at: DeclaredAt | None = field(default=None, compare=False)


def _equal(old: object, new: object) -> bool:
    return old == new


def _decorate(
    args: tuple[type[T], ...], mark: Callable[[type[T]], None]
) -> type[T] | Callable[[type[T]], type[T]]:
    def decorate(cls: type[T]) -> type[T]:
        mark(cls)
        return cls

    if args:
        (cls,) = args
        return decorate(cls)
    return decorate


@overload
def model(
    cls: type[T], /, *, identifier: str | None = None, description: str | None = None
) -> type[T]: ...
@overload
def model(
    *, identifier: str | None = None, description: str | None = None
) -> Callable[[type[T]], type[T]]: ...
@dataclass_transform(field_specifiers=(model_field,))
def model(
    *args: type[T], identifier: str | None = None, description: str | None = None
) -> type[T] | Callable[[type[T]], type[T]]:
    """Declare a class whose instances travel by value; it becomes a dataclass.

    Args:
        identifier: What it travels as, ``@package/key``. Defaults to the app's
            namespace (or the class's module) and the class name.
        description: What it is, for the UI. Defaults to the docstring.
    """
    at = declared_at()

    def mark(cls: type[T]) -> None:
        ensure_model_dataclass(cls)
        stamp(cls, MODEL, ModelMark(identifier, description, at), same=_equal)

    return _decorate(args, mark)


@overload
def state(
    cls: type[T],
    /,
    *,
    name: str | None = None,
    required_locks: Sequence[str] | None = None,
    persist: Literal["synchronous"] | float | None = None,
) -> type[T]: ...
@overload
def state(
    *,
    name: str | None = None,
    required_locks: Sequence[str] | None = None,
    persist: Literal["synchronous"] | float | None = None,
) -> Callable[[type[T]], type[T]]: ...
@dataclass_transform(field_specifiers=(model_field,))
def state(
    *args: type[T],
    name: str | None = None,
    required_locks: Sequence[str] | None = None,
    persist: Literal["synchronous"] | float | None = None,
) -> type[T] | Callable[[type[T]], type[T]]:
    """Declare a state class; it becomes a dataclass.

    Args:
        name: The interface it is published under. Defaults to the class name.
        required_locks: Locks every action taking it holds.
        persist: Keep its last value on disk: ``"synchronous"`` or a period in seconds.
    """
    at = declared_at()

    def mark(cls: type[T]) -> None:
        if not is_dataclass(cls):
            make_dataclass(cls)
        stamp(
            cls,
            STATE,
            StateMark(name, tuple(required_locks or ()), persist, at),
            same=_equal,
        )

    return _decorate(args, mark)


@overload
def context(
    cls: type[T], /, *, name: str | None = None, locks: Sequence[str] | None = None
) -> type[T]: ...
@overload
def context(
    *, name: str | None = None, locks: Sequence[str] | None = None
) -> Callable[[type[T]], type[T]]: ...
def context(
    *args: type[T], name: str | None = None, locks: Sequence[str] | None = None
) -> type[T] | Callable[[type[T]], type[T]]:
    """Declare a context class: an object the agent keeps, handed to what asks for it.

    Args:
        name: What the agent keeps it under. Defaults to the snake_case class name.
        locks: Locks held while a parameter of this class is.
    """
    at = declared_at()

    def mark(cls: type[T]) -> None:
        stamp(cls, CONTEXT, ContextMark(name, tuple(locks or ()), at), same=_equal)

    return _decorate(args, mark)


def declare(
    app: str | None = None,
    *,
    auto_resolvable: bool = False,
    min: int | None = None,
    max: int | None = None,
    version: str | None = None,
) -> Callable[[type[T]], type[T]]:
    """Declare a protocol class: what an app demands of another app.

    Its demands' ports are built when an app using it is assembled.

    Args:
        app: The remote app it is directed at, if any.
        auto_resolvable: Whether any matching agent may be assigned automatically.
        min: Minimum viable number of matching agents.
        max: Maximum viable number of matching agents.
        version: The protocol's version.
    """
    at = declared_at()

    def decorate(cls: type[T]) -> type[T]:
        stamp(
            cls,
            PROTOCOL,
            ProtocolMark(app, auto_resolvable, min, max, version, at),
            same=_equal,
        )
        return cls

    return decorate


def interactions(cls: type[T]) -> type[T]:
    """Declare an interactions protocol: a class of ``@ask`` and ``@inform`` methods."""
    stamp(cls, INTERACTIONS, InteractionsMark(declared_at()), same=_equal)
    return cls


__all__ = [
    "ContextMark",
    "InteractionsMark",
    "ModelMark",
    "ProtocolMark",
    "StateMark",
    "context",
    "declare",
    "interactions",
    "model",
    "state",
]
