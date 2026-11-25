"""What a class declares about itself, kept on the class.

A client, a structure, a model, a state, a protocol, an interactions protocol and a
context each carry their declaration as a class attribute, set by their decorator
(``@client``, ``@structure``, ``@model``, ...). An app finds them by walking the
annotations of what it offers, when it is snapshotted; nothing is registered.

A marker holds only what is written on the class. Whatever is built against an app
(ports, a model's identifier in the app's namespace, a state's schema) is built
when that app is assembled.

Markers are read from the class's own ``__dict__``, never inherited: a subclass of a
client or a structure is not one until it says so.
"""

from collections.abc import Callable
from typing import Any, TypeVar

CLIENT = "__arkitekt_client__"
STRUCTURE = "__arkitekt_structure__"
MODEL = "__arkitekt_model__"
STATE = "__arkitekt_state__"
PROTOCOL = "__arkitekt_protocol__"
INTERACTIONS = "__arkitekt_interactions__"
CONTEXT = "__arkitekt_context__"

ALL = (CLIENT, STRUCTURE, MODEL, STATE, PROTOCOL, INTERACTIONS, CONTEXT)

T = TypeVar("T")

EPOCH = 0
"""Raised by every stamp: an assembled app is stale once it differs."""


class MarkerConflictError(TypeError):
    """A class is declared twice, differently."""


def marker_of(cls: object, name: str) -> Any:  # noqa: ANN401 -- the payload of that marker
    """The marker ``name`` that ``cls`` itself carries, or ``None``."""
    if not isinstance(cls, type):
        return None
    return vars(cls).get(name)


def stamp(
    cls: type[Any],
    name: str,
    payload: T,
    *,
    same: Callable[[T, T], bool] | None = None,
) -> None:
    """Put ``payload`` on ``cls`` as its marker ``name``.

    A second stamp replaces the first when ``same`` says they are the same
    declaration (a module imported again); otherwise it is refused.

    Raises:
        MarkerConflictError: If ``cls`` carries a different declaration already.
    """
    global EPOCH
    existing = vars(cls).get(name)
    if existing is not None and existing is not payload:
        if same is None or not same(existing, payload):
            raise MarkerConflictError(
                f"{cls.__module__}.{cls.__qualname__} is declared twice, differently: "
                f"{existing!r} and {payload!r}."
            )
    # Through `type`: a pydantic model class has no `__setattr__` of its own, but
    # going through the metaclass would be the wrong door if one ever did.
    type.__setattr__(cls, name, payload)
    EPOCH += 1


__all__ = [
    "ALL",
    "CLIENT",
    "CONTEXT",
    "INTERACTIONS",
    "MODEL",
    "PROTOCOL",
    "STATE",
    "STRUCTURE",
    "MarkerConflictError",
    "marker_of",
    "stamp",
]
