"""The structural rules the spec's models carry (arkitekt_spec.rules)."""

import pytest
from pydantic import ValidationError

from arkitekt_spec.actions import (
    ArgPortInput,
    DefinitionInput,
    ImplementAgentInput,
    SliderAssignWidgetInput,
    ValidatorInput,
)
from arkitekt_spec.rules import infer_dependencies


def port(key: str, kind: str = "INT", **extra: object) -> dict[str, object]:
    return {"key": key, "kind": kind, "nullable": False, **extra}


def definition(args: tuple[object, ...] | list[object] = (), **extra: object) -> dict[str, object]:
    return {"key": "f", "version": "1", "name": "F", "kind": "FUNCTION", "args": list(args), **extra}


def test_a_structure_port_needs_an_identifier():
    with pytest.raises(ValidationError, match="identifier"):
        ArgPortInput.model_validate(port("x", "STRUCTURE"))


def test_a_list_has_exactly_one_child():
    with pytest.raises(ValidationError, match="exactly one child"):
        ArgPortInput.model_validate(port("x", "LIST", children=[port("a"), port("b")]))


def test_a_dict_may_have_named_children_but_not_mixed_with_the_item_key():
    ArgPortInput.model_validate(port("x", "DICT", children=[port("a"), port("b")]))
    with pytest.raises(ValidationError, match="homogeneous"):
        ArgPortInput.model_validate(port("x", "DICT", children=[port("..."), port("b")]))


def test_a_default_must_be_json():
    with pytest.raises(ValidationError, match="JSON"):
        ArgPortInput.model_validate(port("x", default=object()))


def test_a_slider_needs_ordered_bounds():
    with pytest.raises(ValidationError, match="min"):
        SliderAssignWidgetInput.model_validate({"min": 2, "max": 1})


def _call(value_path: str):
    return {"operation": "gt", "arguments": [{"key": "a", "valuePath": value_path}]}


def test_a_validator_may_only_reach_its_dependencies():
    ValidatorInput.model_validate({"call": _call("value"), "dependencies": []})
    with pytest.raises(ValidationError, match="not in dependencies"):
        ValidatorInput.model_validate({"call": _call("/other"), "dependencies": []})


def test_dependencies_are_inferred_from_value_paths():
    call = ValidatorInput.model_validate({"call": _call("/other/x"), "dependencies": ["other"]}).call
    assert infer_dependencies(call) == ("other",)


def test_a_definition_dependency_must_resolve_to_a_port():
    validator = {"call": _call("/ghost"), "dependencies": ["ghost"]}
    with pytest.raises(ValidationError, match="invalid dependency: ghost"):
        DefinitionInput.model_validate(definition([port("x", validators=[validator])]))


def test_an_agent_refuses_duplicate_interfaces_and_missing_locks():
    implementation = {"definition": definition(), "interface": "f"}
    with pytest.raises(ValidationError, match="Duplicate implementation interface"):
        ImplementAgentInput.model_validate({"implementations": [implementation, implementation]})
    with pytest.raises(ValidationError, match="references lock 'gpu'"):
        ImplementAgentInput.model_validate(
            {"implementations": [{**implementation, "locks": ["gpu"]}]}
        )
