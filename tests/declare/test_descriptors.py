"""Descriptor constraints are evaluated the way the server evaluates them."""

import pytest

from arkitekt_spec.actions import DescriptorOperator
from arkitekt_spec.declare.annotations import Provides, Requires
from arkitekt_spec.declare.descriptors import holds, resolve_pointer, unfulfilled, unmet_state
from arkitekt_spec.declare.structures.registry import StructureRegistry
from arkitekt_spec.declare.task import StateRequirement


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


def state(path: str, operator: str, value: object = None, name: str = "stage") -> StateRequirement:
    return StateRequirement(state=name, path=path, operator=DescriptorOperator(operator), value=value)


STAGE = {"busy": False, "position": [1.5, 2.0], "mode": "idle", "a/b": {"~x": 1}}


@pytest.mark.parametrize(
    "requirement",
    [
        state("/busy", "EQUALS", False),
        state("/mode", "IN", ["idle", "parked"]),
        state("/position/0", "GTE", 1.0),
        state("/position/1", "LTE", 2.0),
        state("/position", "CONTAINS", 2.0),
        state("/a~1b/~0x", "EQUALS", 1),
        state("/busy", "EXISTS"),
        state("/gone", "EXISTS", False),
    ],
)
def test_a_state_requirement_that_holds_is_met(requirement: StateRequirement) -> None:
    assert unmet_state([requirement], {"stage": STAGE}) == ()


def test_a_state_requirement_that_fails_names_the_value_found() -> None:
    assert unmet_state([state("/busy", "EQUALS", True)], {"stage": STAGE}) == (
        "stage/busy EQUALS True (actual False)",
    )


@pytest.mark.parametrize(
    "path",
    ["/gone", "/position/2", "/position/x", "/position/01", "/busy/deeper"],
)
def test_a_path_the_state_does_not_hold_fails(path: str) -> None:
    (failure,) = unmet_state([state(path, "EQUALS", 1)], {"stage": STAGE})
    assert failure.endswith("(missing)")


def test_a_state_the_agent_does_not_have_fails() -> None:
    (failure,) = unmet_state([state("/busy", "EQUALS", False, name="laser")], {"stage": STAGE})
    assert "no state 'laser'" in failure


def test_exists_false_fails_on_a_present_path() -> None:
    assert unmet_state([state("/busy", "EXISTS", False)], {"stage": STAGE}) != ()


def test_a_comparison_that_cannot_be_made_fails_rather_than_raises() -> None:
    (failure,) = unmet_state([state("/mode", "GTE", 1)], {"stage": STAGE})
    assert "GTE orders numbers" in failure


def test_every_failing_requirement_is_reported() -> None:
    failures = unmet_state(
        [state("/busy", "EQUALS", True), state("/mode", "EQUALS", "idle"), state("/gone", "EXISTS")],
        {"stage": STAGE},
    )
    assert len(failures) == 2


def test_a_state_requirement_path_is_a_pointer() -> None:
    with pytest.raises(ValueError):
        StateRequirement(state="stage", path="busy", operator="EQUALS", value=False)


def test_resolve_pointer_of_the_empty_path_is_the_document() -> None:
    assert resolve_pointer(STAGE, "") is STAGE
