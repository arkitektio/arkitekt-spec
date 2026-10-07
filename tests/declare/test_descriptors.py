"""Descriptor constraints are evaluated the way the server evaluates them."""

import pytest

from arkitekt_spec.actions import DescriptorOperator
from arkitekt_spec.declare.annotations import Provides, Requires
from arkitekt_spec.declare.descriptors import holds, unfulfilled
from arkitekt_spec.declare.structures.registry import StructureRegistry


def requires(key: str, operator: str, value: object = None) -> Requires:
    return Requires(key=key, operator=DescriptorOperator(operator), value=value)


@pytest.mark.parametrize(
    ("operator", "actual", "expected", "result"),
    [
        ("EQUALS", 2, 2, True),
        ("EQUALS", 2, 3, False),
        ("NOT_EQUALS", 2, 3, True),
        ("GTE", 3, 3, True),
        ("GTE", 2, 3, False),
        ("LTE", 1, 1, True),
        ("LTE", 2, 1, False),
        ("IN", "a", ["a", "b"], True),
        ("IN", "c", ["a", "b"], False),
        ("NOT_IN", "c", ["a", "b"], True),
        ("CONTAINS", ["x", "y"], "x", True),
        ("CONTAINS", ["x", "y"], "z", False),
        ("EXISTS", 0, None, True),
        ("EXISTS", 0, True, True),
        ("EXISTS", 0, False, False),
        # The server compiles MATCHES to `==`: a pattern is not a pattern here either.
        ("MATCHES", "abc", "abc", True),
        ("MATCHES", "abc", "a.c", False),
    ],
)
def test_an_operator_holds_as_on_the_server(operator: str, actual: object, expected: object, result: bool) -> None:
    assert holds(operator, actual, expected) is result
    assert holds(DescriptorOperator(operator), actual, expected) is result


def test_an_operator_refuses_values_it_cannot_compare() -> None:
    with pytest.raises(TypeError, match="orders numbers"):
        holds("GTE", "three", 3)
    with pytest.raises(TypeError, match="list of allowed values"):
        holds("IN", "a", "abc")
    with pytest.raises(TypeError, match="list descriptor"):
        holds("CONTAINS", "abc", "a")
    with pytest.raises(ValueError, match="Unknown descriptor operator"):
        holds("NEARLY", 1, 1)


def test_failures_name_the_constraint_and_the_actual_value() -> None:
    constraints = [requires("@t/space", "EQUALS", 2), requires("@t/channels", "LTE", 1)]
    assert unfulfilled(constraints, {"@t/space": 2, "@t/channels": 1}) == ()
    assert unfulfilled(constraints, {"@t/space": 3, "@t/channels": 4}) == (
        "@t/space EQUALS 2 (actual 3)",
        "@t/channels LTE 1 (actual 4)",
    )


def test_a_key_the_object_does_not_carry_is_not_tested() -> None:
    constraints = [requires("@t/kind", "EQUALS", "categorical"), requires("@t/space", "EQUALS", 2)]
    assert unfulfilled(constraints, {"@t/space": 2}) == ()
    assert unfulfilled(constraints, {"@t/space": 2, "@t/kind": "continuous"}) == (
        "@t/kind EQUALS 'categorical' (actual 'continuous')",
    )


def test_provides_are_tested_like_requires() -> None:
    provides = [Provides(key="@t/space", operator=DescriptorOperator.GTE, value=3)]
    assert unfulfilled(provides, {"@t/space": 2}) == ("@t/space GTE 3 (actual 2)",)


class Thing:
    def __init__(self, id: str) -> None:
        self.id = id


def test_a_structure_keeps_its_describer_through_merge_and_copy() -> None:
    registry = StructureRegistry()

    def describe(thing: Thing) -> dict[str, int]:
        return {"@t/length": len(thing.id)}

    @registry.structure("@t/thing", describe=describe)
    async def expand_thing(id: str) -> Thing:
        return Thing(id)

    assert registry.get_fullfilled_structure("@t/thing").describe is describe

    other = StructureRegistry()
    other.merge(registry)
    assert other.get_fullfilled_structure("@t/thing").describe is describe


def test_a_structure_without_a_describer_has_none() -> None:
    registry = StructureRegistry()

    @registry.structure("@t/thing")
    async def expand_thing(id: str) -> Thing:
        return Thing(id)

    assert registry.get_fullfilled_structure("@t/thing").describe is None
