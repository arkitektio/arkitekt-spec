"""Edge cases in defining and (de)serializing primitive ports."""

from collections.abc import AsyncIterator, Generator
from enum import Enum
from typing import Literal, Protocol

from arkitekt_spec.actions import ActionKind, PortKind, definition_hash
from arkitekt_spec.declare.annotations.parsers import PortAnnotations, extract_annotations
from arkitekt_spec.declare.definition.define import prepare_definition
from arkitekt_spec.declare.structures.registry import StructureRegistry


def float_with_int_default(x: float = 1) -> float:
    """Float"""
    return x


def list_with_empty_default(x: list[int] = []) -> int:  # noqa: B006
    """List"""
    return len(x)


def takes_float(x: float) -> float:
    """Float"""
    return x


def takes_literal(x: Literal[1, 2]) -> int:
    """Literal"""
    return x


def maybe_nothing() -> int | None:
    """Maybe"""
    return None


def returns_int(x: int) -> int:
    """Same"""
    return x


class Color(str, Enum):
    RED = "red"


def str_enum_with_default(x: Color = Color.RED) -> str:
    """Enum"""
    return x


def test_annotation_decides_over_default_type() -> None:
    definition = prepare_definition(float_with_int_default, StructureRegistry())
    assert definition.args[0].kind == PortKind.FLOAT


def test_enum_with_default_stays_an_enum() -> None:
    definition = prepare_definition(str_enum_with_default, StructureRegistry())
    assert definition.args[0].kind == PortKind.ENUM


def test_empty_list_default_is_kept() -> None:
    definition = prepare_definition(list_with_empty_default, StructureRegistry())
    assert definition.args[0].default == []










def test_hash_tells_functions_and_generators_apart() -> None:
    registry = StructureRegistry()
    function = prepare_definition(returns_int, registry)
    generator = function.model_copy(update={"kind": ActionKind.GENERATOR})
    assert definition_hash(function) != definition_hash(generator)


class Streams(Protocol):
    def words(self, text: str) -> Generator[str, None, None]:
        """Words"""
        ...

    async def count(self, to: int) -> AsyncIterator[str]:
        """Count"""
        ...


def test_a_stub_annotated_as_a_generator_streams() -> None:
    """A protocol method has no body to inspect: its annotation says it streams."""
    for method in (Streams.words, Streams.count):
        definition = prepare_definition(method, StructureRegistry(), omitfirst=1)
        assert definition.kind == ActionKind.GENERATOR
        assert [r.kind for r in definition.returns] == [PortKind.STRING]


async def yields_words(text: str) -> AsyncIterator[str]:
    """Words"""
    for word in text.split():
        yield word


def test_an_async_iterator_annotation_is_what_it_yields() -> None:
    definition = prepare_definition(yields_words, StructureRegistry())
    assert definition.kind == ActionKind.GENERATOR
    assert [r.kind for r in definition.returns] == [PortKind.STRING]


def test_annotation_parsing_leaves_callers_lists_alone() -> None:
    validators: list = []
    effects: list = []
    extract_annotations([], PortAnnotations(validators=validators, effects=effects))
    base = PortAnnotations(validators=validators, effects=effects)
    result = extract_annotations([], base)
    assert result.validators is not validators and result.effects is not effects


def takes_flag(x: bool) -> bool:
    """Flag"""
    return x




def returns_flag() -> bool:
    """Flag"""
    return True


