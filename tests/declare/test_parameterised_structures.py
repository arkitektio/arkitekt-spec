"""A structure registered for a generic class is found under its parameterised form.

``NDArray[np.float64]`` is ``np.ndarray`` parameterised: an app typing its arrays
precisely must still get the structure registered for ``np.ndarray``.
"""

from typing import Generic, TypeVar

from arkitekt_spec.actions import PortKind
from arkitekt_spec.declare.definition.define import prepare_definition
from arkitekt_spec.declare.structures.registry import StructureRegistry

T = TypeVar("T")


class Box(Generic[T]):
    """Holds a value; only ever passed by reference."""


def open_box(box: Box[int]) -> Box[str]:
    """Opens a box."""
    return Box()


def test_a_parameterised_annotation_finds_its_origins_structure() -> None:
    registry = StructureRegistry()
    registry.register_as_memory_structure(Box)

    assert registry.find_for_cls(Box[int]) is registry.find_for_cls(Box)

    definition = prepare_definition(open_box, structure_registry=registry)
    (arg,) = definition.args
    (ret,) = definition.returns
    assert arg.kind == ret.kind == PortKind.MEMORY_STRUCTURE
    assert arg.identifier == ret.identifier == registry.get_identifier_for_cls(Box)


def test_an_unregistered_origin_is_still_unregistered() -> None:
    assert StructureRegistry().find_for_cls(Box[int]) is None
