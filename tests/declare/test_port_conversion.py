"""Unit tests for type-hint → port conversion edge cases."""

from typing import Annotated

from arkitekt_spec.actions import (
    DescriptorOperator,
    PortKind,
    StringAssignWidgetInput,
)
from arkitekt_spec.declare.annotations import Provides, Requires
from arkitekt_spec.declare.definition.define import prepare_definition
from arkitekt_spec.declare.structures.registry import StructureRegistry

TiffName = Annotated[
    str, Requires(key="filename", operator=DescriptorOperator.MATCHES, value=r".*\.tiff?")
]
Wide = Annotated[int, Provides(key="x", operator=DescriptorOperator.GTE, value="1")]


class Box:
    """A model, declared on the registry each test builds."""

    width: int
    height: int


def registry_with_box() -> StructureRegistry:
    registry = StructureRegistry()
    registry.model(Box)
    return registry


def optional_requires(name: TiffName | None = None) -> Wide | None:
    """Optional ports keep their descriptors."""
    return None


def model_requires(
    box: Annotated[
        Box, Requires(key="kind", operator=DescriptorOperator.MATCHES, value="box")
    ],
) -> int:
    """Model arg ports keep their descriptors."""
    return 1


def test_optional_ports_keep_requires_and_provides() -> None:
    definition = prepare_definition(
        optional_requires, structure_registry=StructureRegistry()
    )
    (arg,) = definition.args
    (ret,) = definition.returns
    assert arg.nullable and arg.requires and arg.requires[0].key == "filename"
    assert ret.nullable and ret.provides and ret.provides[0].key == "x"


def test_model_arg_port_keeps_requires() -> None:
    definition = prepare_definition(
        model_requires, structure_registry=registry_with_box()
    )
    (arg,) = definition.args
    assert arg.kind == PortKind.MODEL
    assert arg.requires and arg.requires[0].key == "kind"


def test_prepare_definition_does_not_consume_caller_maps() -> None:
    widgets = {"name": StringAssignWidgetInput()}
    prepare_definition(
        optional_requires, structure_registry=StructureRegistry(), widgets=widgets
    )
    assert "name" in widgets, "prepare_definition popped from the caller's dict"
