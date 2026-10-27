"""Named returns: ``Annotated[T, Key("count")]`` and a ``NamedTuple`` key the return ports.

A numpy docstring's return names stay labels, so an action that names its returns only there
keeps ``return<N>`` keys and the hash it had.
"""

from collections.abc import AsyncIterator
from typing import Annotated, NamedTuple

import pytest

from arkitekt_spec.actions import PortKind, ReturnPortInput
from arkitekt_spec.declare.annotations import Description, Key
from arkitekt_spec.declare.definition.define import prepare_definition
from arkitekt_spec.declare.definition.errors import DefinitionError
from arkitekt_spec.declare.structures.registry import StructureRegistry


def returns_of(function: object) -> list[tuple[str, str | None, PortKind]]:
    definition = prepare_definition(function, structure_registry=StructureRegistry())  # type: ignore[arg-type]
    return [(port.key, port.label, port.kind) for port in definition.returns]


def counted() -> Annotated[int, Key("count")]:
    """Count"""
    return 1


def maybe_counted() -> Annotated[int | None, Key("count"), Description("How many")]:
    """Count"""
    return None


def pair() -> tuple[Annotated[int, Key("count")], str]:
    """Pair"""
    return 1, "a"


class Measured(NamedTuple):
    count: int
    mean: float


def measured() -> Measured:
    """Measure"""
    return Measured(1, 2.0)


async def streamed() -> AsyncIterator[Measured]:
    """Stream"""
    yield Measured(1, 2.0)


def numpy_named() -> int:
    """Numpy

    Returns
    -------
    count : int
        the count
    """
    return 1


def numpy_named_pair() -> Measured:
    """Numpy

    Returns
    -------
    n : int
        the count
    m : float
        the mean
    """
    return Measured(1, 2.0)


def test_key_names_the_return() -> None:
    assert returns_of(counted) == [("count", None, PortKind.INT)]


def test_key_names_an_optional_return_beside_other_markers() -> None:
    definition = prepare_definition(maybe_counted, structure_registry=StructureRegistry())
    (port,) = definition.returns
    assert (port.key, port.nullable, port.description) == ("count", True, "How many")


def test_key_names_one_element_of_a_tuple() -> None:
    assert returns_of(pair) == [("count", None, PortKind.INT), ("return1", None, PortKind.STRING)]


def test_a_named_tuple_is_one_return_per_field() -> None:
    assert returns_of(measured) == [("count", None, PortKind.INT), ("mean", None, PortKind.FLOAT)]


def test_a_generator_of_named_tuples_yields_one_return_per_field() -> None:
    assert returns_of(streamed) == [("count", None, PortKind.INT), ("mean", None, PortKind.FLOAT)]


def test_a_numpy_return_name_is_only_a_label() -> None:
    assert returns_of(numpy_named) == [("return0", "count", PortKind.INT)]


def test_numpy_names_label_a_named_tuple_in_order() -> None:
    assert returns_of(numpy_named_pair) == [("count", "n", PortKind.INT), ("mean", "m", PortKind.FLOAT)]


def test_an_unnamed_return_is_the_port_it_was() -> None:
    def plain() -> int:
        """Plain"""
        return 1

    def named() -> Annotated[int, Key("return0")]:
        """Plain"""
        return 1

    registry = StructureRegistry()
    assert prepare_definition(plain, structure_registry=registry).returns == prepare_definition(named, structure_registry=registry).returns
    assert prepare_definition(plain, structure_registry=registry).returns == (ReturnPortInput(key="return0", kind=PortKind.INT, nullable=False, effects=()),)


def test_two_returns_of_one_key_are_refused() -> None:
    def twice() -> tuple[Annotated[int, Key("n")], Annotated[int, Key("n")]]:
        """Twice"""
        return 1, 2

    with pytest.raises(DefinitionError, match="names two returns 'n'"):
        prepare_definition(twice, structure_registry=StructureRegistry())


def test_a_key_on_an_argument_is_refused() -> None:
    def keyed(x: Annotated[int, Key("y")]) -> int:
        """Keyed"""
        return x

    with pytest.raises(DefinitionError, match="keyed by its parameter name"):
        prepare_definition(keyed, structure_registry=StructureRegistry())


def test_two_keys_on_one_return_are_refused() -> None:
    def keyed() -> Annotated[int, Key("a"), Key("b")]:
        """Keyed"""
        return 1

    with pytest.raises(DefinitionError, match="Multiple Keys"):
        prepare_definition(keyed, structure_registry=StructureRegistry())


def test_a_key_must_be_an_identifier() -> None:
    with pytest.raises(TypeError, match="identifier"):
        Key("not a key")
