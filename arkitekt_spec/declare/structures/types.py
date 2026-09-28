"""Types for the structures module."""

import dataclasses
import re
from collections.abc import Awaitable, Callable, Sequence
from enum import Enum
from typing import (
    Any,
    Protocol,
    TypeVar,
    cast,
    runtime_checkable,
)

from pydantic import BaseModel, ConfigDict, Field, model_validator

from arkitekt_spec.actions import (
    AssignWidgetInput,
    ChoiceInput,
    ReturnWidgetInput,
    StateDefinitionInput,
)
from arkitekt_spec.scalars import ID

from .errors import StructureDefinitionError

JSONSerializable = (
    str
    | int
    | float
    | bool
    | None
    | dict[str, "JSONSerializable"]
    | list["JSONSerializable"]
)


@runtime_checkable
class Expandable(Protocol):
    """A callable that takes a set of keyword arguments to initialize the object."""

    def __init__(self, value: Any) -> None:
        """Initialize the Expandable with the value."""
        ...


@runtime_checkable
class Shrinker(Protocol):
    """A callable that takes a value and returns a string representation of it that
    can be serialized to json."""

    def __call__(self, value: Any, /) -> Awaitable[str]:
        """Convert a value to a string representation."""

        ...


@runtime_checkable
class Predicator(Protocol):
    """A callable that takes a value and returns True if the value is of the
    correct type for the structure."""

    def __call__(self, value: Any, /) -> bool:
        """Check if the value is of the correct type for the structure."""

        ...


@runtime_checkable
class Expander(Protocol):
    """A callable that takes a string and returns the original value,
    which can be deserialized from json."""

    def __call__(self, id: ID, /) -> Awaitable[Any]:
        """Convert a string representation back to the original value."""

        ...


ExpanderT = TypeVar("ExpanderT", bound=Callable[..., Awaitable[Any]])
"""The expander exactly as declared, clients and all.

An expander is called with the id alone (see :class:`Expander`); the clients it
asks for after that (``expand(id, mikro: Mikro)``) are bound when a run starts.
A decorator that hands the function back keeps its declared type through this.
"""


@runtime_checkable
class ManyExpander(Protocol):
    """Expands several ids in one go, answering in their order.

    ``None`` stands for an id there is no object for. A structure gets one either
    by declaring it or, when its expander asks for a client that offers a batch
    call, from that client when the registry is bound.
    """

    def __call__(self, ids: Sequence[ID], /) -> Awaitable[Sequence[Any]]:
        """Convert the ids back to the original values."""

        ...


IDENTIFIER_PATTERN = re.compile(r"^@[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
"""What the rekuest server accepts as a structure identifier: ``@package/key``.

Kept identical to ``IDENTIFIER_PATTERN`` in the server's
``rekuest_core/inputs/models.py``. Anything else is refused when the agent
registers its implementations, long after and far from where it was written.
"""


def is_valid_identifier(identifier: str) -> bool:
    """Whether the rekuest server would accept ``identifier`` for a structure."""
    return bool(IDENTIFIER_PATTERN.match(identifier))


def service_from_identifier(identifier: str) -> str | None:
    """The service a structure identifier belongs to: ``@mikro/arraydataset`` -> ``mikro``.

    The name is the one the service is built under in an app's ``services`` map.
    Identifiers without the ``@<service>/`` prefix belong to no service.
    """
    if not identifier.startswith("@") or "/" not in identifier:
        return None
    return identifier[1:].split("/", 1)[0] or None


class FullFilledStructure(BaseModel):
    """A structure registered for (de)serialization: what travels by id, and how.

    A structure carries its own expander, which may ask for the clients it needs
    by annotation (``expand(id, mikro: Mikro)``); a run injects them when it binds
    the registry. There is one way to declare a structure, and this is what it
    becomes.

    Expansion is **plural**: :meth:`expand_many` is the operation, and
    :meth:`expand` is the degenerate case of one id. What fetches many is decided
    once, when the registry is bound -- an explicit ``aexpand_many``, else the
    injected client's own batch call, else one request per id.
    """

    cls: type[object]
    identifier: str
    description: str | None
    predicate: Callable[[Any], bool]
    convert_default: Callable[[Any], str] | None
    default_widget: AssignWidgetInput | None
    default_returnwidget: ReturnWidgetInput | None
    aexpand: Expander | None = None
    """Turns one id into the object."""
    ashrink: Shrinker | None = None
    """Turns the object into its id. ``None`` reads ``obj.id``, which is what
    every client does; no SDK has ever needed anything else."""
    aexpand_many: ManyExpander | None = None
    """Turns several ids into their objects in one request.

    Set either by the structure itself or, when it asks for a client that offers
    one, by :meth:`StructureRegistry.bind`. ``None`` means one request per id.
    """
    injects: dict[str, type] = Field(default_factory=dict, exclude=True)
    """The clients this structure's functions ask for.

    Read off the expander's annotations when it is registered
    (``expand(id, mikro: Mikro)`` gives ``{"mikro": Mikro}``), and applied to the
    functions by :meth:`StructureRegistry.bind` when a run starts.
    """
    service: str | None = None
    """The service that owns this structure, and whose client expands it.

    Stamped by :meth:`AppRegistry.merge` when a service's registry is taken in
    (``declared`` is then true). Only a structure registered outside a service
    falls back to the identifier's ``@<service>/`` prefix, which is a guess --
    unlok's structure is ``@lok/service`` under the service ``unlok``.
    """
    declared: bool = False
    """Whether ``service`` was stamped by the service that brought this structure
    in, rather than guessed from the identifier. Only a declared structure names
    a service an app can actually be built with."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    @model_validator(mode="after")
    def _derive_service(self) -> "FullFilledStructure":
        if not is_valid_identifier(self.identifier):
            raise StructureDefinitionError(
                f"'{self.identifier}' is not a structure identifier. The rekuest "
                f"server only accepts the form '@package/key' (for {self.cls!r})."
            )
        if self.service is None:
            self.service = service_from_identifier(self.identifier)
        if self.aexpand is None and self.aexpand_many is None:
            raise StructureDefinitionError(
                f"'{self.identifier}' has no way to expand an id. Give it an "
                "`expand` function; a structure that cannot be fetched back is a "
                "memory structure."
            )
        return self

    async def expand(self, id: ID) -> Any:
        """Expand ``id`` into the object it names.

        The degenerate case of :meth:`expand_many`, except where the structure
        has a singular expander to call directly.

        Args:
            id: The id to expand.

        Returns:
            The object.
        """
        if self.aexpand is not None:
            return await self.aexpand(id)
        (found,) = await self.expand_many([id])
        return found

    async def expand_many(self, ids: Sequence[ID]) -> list[Any]:
        """Expand ``ids`` into their objects, in order.

        Args:
            ids: The ids to expand.

        Returns:
            One value per id, in order.

        Raises:
            ValueError: If a batch expander answers a different number of values.
        """
        if self.aexpand_many is None:
            import asyncio

            assert self.aexpand is not None  # checked when the structure was built
            return list(await asyncio.gather(*[self.aexpand(id) for id in ids]))

        found = list(await self.aexpand_many(ids))
        if len(found) != len(ids):
            raise ValueError(
                f"{self.identifier}: asked for {len(ids)} ids and got "
                f"{len(found)} back. `expand_many` answers one per id."
            )
        return found

    async def shrink(self, obj: Any) -> ID:
        """Shrink ``obj`` to the id it travels as.

        Args:
            obj: The object to shrink.

        Returns:
            Its id.
        """
        if self.ashrink is None:
            return cast(ID, obj.id)
        return await self.ashrink(obj)


class FullFilledEnum(BaseModel):
    """An enum port: a closed set of named choices that travel by name.

    Built from an ``Enum`` subclass or a ``typing.Literal[...]``. A Literal stays
    a Literal: it is the port's ``cls`` as written on the function, and its
    members are the bare literal values, so the function receives ``"upper"``
    and not a member of some synthetic enum class. What differs between the two
    is only :attr:`members`; the wire form is a rekuest enum either way.
    """

    cls: Any
    """The ``Enum`` subclass, or the ``Literal[...]`` annotation itself."""
    identifier: str
    description: str | None
    choices: list[ChoiceInput]
    members: dict[str, Any]
    """The value handed to the function for each choice name on the wire.

    For an ``Enum`` these are its members; for a ``Literal`` the literal values.
    """
    predicate: Predicator
    convert_default: Callable[[Any], str]
    default_widget: AssignWidgetInput | None
    default_returnwidget: ReturnWidgetInput | None
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    def expand(self, value: str | int) -> Any:
        """The member a wire value names.

        A string is a choice name. An int is accepted for members whose value
        is that int (an ``IntEnum`` member, or an int literal).

        Raises:
            KeyError: If no member matches.
        """
        if isinstance(value, str) and value in self.members:
            return self.members[value]
        for member in self.members.values():
            if _member_value(member) == value:
                return member
        raise KeyError(value)


def _member_value(member: Any) -> Any:
    """The bare value of an enum member, or the literal value itself."""
    return member.value if isinstance(member, Enum) else member


class FullFilledMemoryStructure(BaseModel):
    """A fullfiled memory structure that can be used to serialize and deserialize"""

    cls: Any
    identifier: str
    predicate: Predicator
    description: str | None
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")


class FullFilledModel(BaseModel):
    """A fullfiled model that can be used to serialize and deserialize"""

    cls: type[Expandable]
    identifier: str
    predicate: Predicator
    description: str | None = Field(default=None)
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")


FullFilledType = (
    FullFilledStructure | FullFilledEnum | FullFilledMemoryStructure | FullFilledModel
)


@dataclasses.dataclass(frozen=True)
class StateDeclaration:
    """What an app declared about a state class: its interface, schema and rules.

    Kept by the app's structure registry under the class, so that a parameter
    annotated with the class is recognised as that state and an agent knows how
    to make an instance of it evented when it adopts one. Nothing is written on
    the class.
    """

    interface: str
    definition: StateDefinitionInput
    required_locks: tuple[str, ...] = ()
    publish_interval: float = 0.1


@dataclasses.dataclass(frozen=True)
class ContextDeclaration:
    """What an app declared about a context class: the name it is kept under
    and the locks its use requires.

    Kept by the app's structure registry under the class, so that a parameter
    annotated with the class is handed the agent's context of that name. Nothing
    is written on the class.
    """

    name: str
    locks: tuple[str, ...] = ()
