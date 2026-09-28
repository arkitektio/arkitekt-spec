"""The class a structure travels as is read off its expander's return annotation."""

from typing import Any

import pytest

from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.declare.structures.errors import StructureDefinitionError


class Thing:
    """A thing that travels by id."""

    def __init__(self, id: str) -> None:
        self.id = id


class Other:
    """Another class altogether."""


class ThingClient:
    """The client a service returns."""

    async def aget_thing(self, id: str) -> Thing:
        return Thing(id)


def registry_with_client() -> AppRegistry:
    registry = AppRegistry()

    @registry.service()
    def things() -> ThingClient:
        return ThingClient()

    return registry


def test_class_is_inferred_from_the_return_annotation() -> None:
    registry = registry_with_client()

    @registry.structure("@things/thing")
    async def expand_thing(id: str, things: ThingClient) -> Thing:
        return await things.aget_thing(id)

    assert (
        registry.structure_registry.get_fullfilled_structure("@things/thing").cls
        is Thing
    )
    assert registry.structure_registry.find_for_cls(Thing) is not None


def test_explicit_class_covers_an_untyped_expander() -> None:
    registry = registry_with_client()

    @registry.structure("@things/thing", cls=Thing)
    async def expand_thing(id: str, things: ThingClient) -> Any:
        return await things.aget_thing(id)

    assert (
        registry.structure_registry.get_fullfilled_structure("@things/thing").cls
        is Thing
    )


def test_no_class_anywhere_is_refused() -> None:
    registry = registry_with_client()
    with pytest.raises(StructureDefinitionError, match="does not say what it returns"):

        @registry.structure("@things/thing")
        async def expand_thing(id: str, things: ThingClient):
            return await things.aget_thing(id)


def test_a_non_class_annotation_needs_cls() -> None:
    registry = registry_with_client()
    with pytest.raises(StructureDefinitionError, match="is not a class"):

        @registry.structure("@things/thing")
        async def expand_thing(id: str, things: ThingClient) -> Thing | None:
            return await things.aget_thing(id)


def test_class_and_annotation_must_agree() -> None:
    registry = registry_with_client()
    with pytest.raises(StructureDefinitionError, match="One structure, one class"):

        @registry.structure("@things/thing", cls=Other)
        async def expand_thing(id: str, things: ThingClient) -> Thing:
            return await things.aget_thing(id)
