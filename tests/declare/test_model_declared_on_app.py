"""A model is declared on an app; nothing registers itself on sight.

``@app.model`` makes the class a dataclass if it is not one and registers it on
that app's structures. A port annotated with it is a MODEL port with one child
per field. A dataclass no app declared is refused when a definition names it,
in its own terms, and so is a model nested in a model.
"""

import dataclasses
from dataclasses import dataclass

import pytest

from arkitekt_spec.actions import PortKind
from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.declare.definition.define import prepare_definition
from arkitekt_spec.declare.definition.errors import DefinitionError
from arkitekt_spec.declare.errors import RegistryFrozenError
from arkitekt_spec.declare.structures.model import model_field
from arkitekt_spec.declare.structures.registry import StructureRegistry


class Inner:
    """An inner model."""

    n: int


class Outer:
    """An outer model."""

    inner: Inner
    threshold: float = model_field(default=0.5, description="Cut-off")


def test_declaring_a_model_makes_a_dataclass_and_registers_it() -> None:
    registry = AppRegistry()
    assert registry.model(Inner) is Inner

    assert dataclasses.is_dataclass(Inner)
    assert not any(name.startswith("__rekuest") for name in vars(Inner))
    declared = registry.structure_registry.model_for(Inner)
    assert declared is not None
    assert (declared.identifier, declared.description) == ("inner", "An inner model.")
    assert Inner(n=1) == Inner(n=1)


def test_identifier_and_description_can_be_given() -> None:
    registry = StructureRegistry()

    @registry.model(identifier="the_inner", description="Given")
    class Given:
        n: int

    declared = registry.model_for(Given)
    assert declared is not None
    assert (declared.identifier, declared.description) == ("the_inner", "Given")


def test_an_action_naming_a_declared_model_gets_a_model_port_with_children() -> None:
    registry = StructureRegistry()
    registry.model(Inner)
    registry.model(Outer)

    def use(outer: Outer) -> Inner:
        """Use."""
        return outer.inner

    definition = prepare_definition(use, structure_registry=registry)
    (arg,) = definition.args
    assert (arg.kind, arg.identifier) == (PortKind.MODEL, "outer")
    assert arg.children is not None
    by_key = {child.key: child for child in arg.children}
    assert by_key["inner"].kind == PortKind.MODEL
    assert by_key["inner"].identifier == "inner"
    assert by_key["threshold"].description == "Cut-off"
    assert definition.returns[0].kind == PortKind.MODEL


def test_an_undeclared_dataclass_is_refused_in_its_own_terms() -> None:
    @dataclass
    class Loose:
        n: int

    def use(loose: Loose) -> int:
        """Use."""
        return loose.n

    with pytest.raises(DefinitionError, match=r"app\.model\(Loose\)"):
        prepare_definition(use, structure_registry=StructureRegistry())


def test_a_model_nested_in_a_model_has_to_be_declared_too() -> None:
    registry = StructureRegistry()
    registry.model(Outer)  # Inner is a dataclass by now, but not declared here

    def use(outer: Outer) -> int:
        """Use."""
        return 1

    with pytest.raises(DefinitionError, match=r"app\.model\(Inner\)"):
        prepare_definition(use, structure_registry=registry)


def test_a_model_of_one_app_is_unknown_to_another() -> None:
    a, b = StructureRegistry(), StructureRegistry()
    a.model(Inner)

    assert a.model_for(Inner) is not None
    assert b.model_for(Inner) is None


def test_a_frozen_registry_refuses_a_model() -> None:
    registry = AppRegistry()
    registry.freeze()

    with pytest.raises(RegistryFrozenError):
        registry.model(Inner)
