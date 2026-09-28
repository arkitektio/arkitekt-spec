"""Nothing registers itself: what a port may name has to be registered first.

The one exception is enums, ``Literal`` included. They travel by value and every
part of them -- the choices, the converter, the predicate -- is read off the
annotation, so there is nothing to declare and nothing to get wrong.

Everything that carries an *object* is refused until it is registered, because
getting it wrong is silent: a structure invented from a class's own name is one
the server rejects much later, and a plain class demoted to a memory structure is
an object parked in this agent that no other app can ever read.
"""

from enum import Enum
from typing import Literal

import pytest

from arkitekt_spec.declare.structures.errors import (
    StructureDefinitionError,
    StructureRegistryError,
)
from arkitekt_spec.declare.structures.registry import StructureRegistry
from arkitekt_spec.declare.structures.types import (
    FullFilledEnum,
    FullFilledMemoryStructure,
    FullFilledStructure,
)


class ColorEnum(Enum):
    """A color choice"""

    RED = "red"
    GREEN = "green"


class GlobalThing:
    """A global structure implementing the identifier protocol."""

    def __init__(self, id: str) -> None:
        """Initialize the object with an id."""
        self.id = id

    @classmethod
    def get_identifier(cls) -> str:
        """Get the identifier of the object."""
        return "@mock/globalthing"

    async def ashrink(self) -> str:
        """Shrink the object to its id."""
        return self.id

    @classmethod
    async def aexpand(cls, value: str) -> "GlobalThing":
        """Expand the object from its id."""
        return cls(id=value)

    @classmethod
    def convert_default(cls, value: "GlobalThing") -> str:
        """Convert a default value to its id."""
        return f"converted-{value.id}"


class UnnameableThing:
    """Carries the protocol, but names itself something that cannot travel."""

    @classmethod
    def get_identifier(cls) -> str:
        """Not of the form the server accepts."""
        return "UnnameableThing"

    async def ashrink(self) -> str:
        """Shrink the object."""
        return "x"

    @classmethod
    async def aexpand(cls, value: str) -> "UnnameableThing":
        """Expand the object."""
        return cls()


class PlainThing:
    """A plain class, which can only ever live on this agent's shelve."""



# --------------------------------------------------------------------------- #
# Enums still derive
# --------------------------------------------------------------------------- #


def test_an_enum_derives_without_being_registered() -> None:
    """Everything about it is on the annotation, so there is nothing to declare."""
    registry = StructureRegistry()

    fullfilled = registry.get_fullfilled_type_for_cls(ColorEnum)

    assert isinstance(fullfilled, FullFilledEnum)
    assert [c.label for c in fullfilled.choices] == ["RED", "GREEN"]
    assert fullfilled.convert_default(ColorEnum.RED) == "RED"


def test_a_literal_derives_too() -> None:
    """There is no class to preregister, so requiring one would ban the annotation."""
    registry = StructureRegistry()

    fullfilled = registry.get_fullfilled_type_for_cls(Literal["a", "b"])

    assert isinstance(fullfilled, FullFilledEnum)
    assert sorted(c.label for c in fullfilled.choices) == ["a", "b"]


# --------------------------------------------------------------------------- #
# Everything that carries an object is refused until registered
# --------------------------------------------------------------------------- #


def test_a_fetchable_class_is_refused_until_it_is_registered() -> None:
    """It can fetch itself, so it looks ready -- but nothing said it belongs here."""
    registry = StructureRegistry()

    with pytest.raises(StructureRegistryError) as raised:
        registry.get_fullfilled_type_for_cls(GlobalThing)

    assert "nothing declared it" in str(raised.value)
    assert registry.find_for_cls(GlobalThing) is None


def test_register_from_protocol_takes_the_class_at_its_word() -> None:
    """The one-liner that replaces what auto-registration used to do unasked."""
    registry = StructureRegistry()

    registry.register_from_protocol(GlobalThing)

    fullfilled = registry.get_fullfilled_type_for_cls(GlobalThing)
    assert isinstance(fullfilled, FullFilledStructure)
    assert fullfilled.identifier == "@mock/globalthing"
    # A class's own converter is used, not the identity one.
    assert fullfilled.convert_default is not None
    assert fullfilled.convert_default(GlobalThing(id="x")) == "converted-x"


def test_register_from_protocol_still_refuses_a_name_that_cannot_travel() -> None:
    """Asking explicitly does not buy an identifier the server would reject."""
    registry = StructureRegistry()

    with pytest.raises(StructureDefinitionError, match="@package/key"):
        registry.register_from_protocol(UnnameableThing)


def test_a_plain_class_is_refused_and_says_how_to_ask_for_it() -> None:
    registry = StructureRegistry()

    with pytest.raises(StructureRegistryError) as raised:
        registry.get_fullfilled_type_for_cls(PlainThing)

    message = str(raised.value)
    assert "not registered" in message
    assert "register_memory_structure(PlainThing)" in message
    assert registry.identifier_memory_structure_map == {}


def test_a_registered_memory_structure_keeps_the_derived_identifier() -> None:
    """The name is unchanged; only the asking is new."""
    registry = StructureRegistry()

    registry.register_as_memory_structure(PlainThing)

    fullfilled = registry.get_fullfilled_type_for_cls(PlainThing)
    assert isinstance(fullfilled, FullFilledMemoryStructure)
    assert fullfilled.identifier == f"@{PlainThing.__module__.lower()}/plainthing"


def test_a_memory_structure_can_be_named_and_described() -> None:
    registry = StructureRegistry()

    registry.register_as_memory_structure(
        PlainThing, identifier="@myapp/thing", description="A thing"
    )

    fullfilled = registry.get_fullfilled_type_for_cls(PlainThing)
    assert isinstance(fullfilled, FullFilledMemoryStructure)
    assert fullfilled.identifier == "@myapp/thing"
    assert fullfilled.description == "A thing"
