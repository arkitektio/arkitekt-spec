"""The class a structure travels as is read off its expander's return annotation."""

from typing import Any

import pytest

from arkitekt_spec.declare.client import client, client_of
from arkitekt_spec.declare.structures.declare import structure, structure_of
from arkitekt_spec.declare.structures.errors import StructureDefinitionError


class Thing:
    """A thing that travels by id."""

    def __init__(self, id: str) -> None:
        self.id = id


class Other:
    """Another class altogether."""


@client(requires={})
class ThingClient:
    """The client the structure's expander asks for."""

    async def aget_thing(self, id: str) -> Thing:
        return Thing(id)


def test_class_is_inferred_from_the_return_annotation() -> None:
    @structure("@things/thing")
    async def expand_thing(id: str, things: ThingClient) -> Thing:
        return await things.aget_thing(id)

    declared = structure_of(Thing)
    assert declared is not None and declared.identifier == "@things/thing"
    owner = client_of(ThingClient)
    assert owner is not None and declared.owner is owner
    assert declared in owner.structures


def test_explicit_class_covers_an_untyped_expander() -> None:
    @structure("@things/other", cls=Other)
    async def expand_other(id: str, things: ThingClient) -> Any:  # noqa: ANN401 -- what this test is about: an expander annotated Any
        return Other()

    declared = structure_of(Other)
    assert declared is not None and declared.cls is Other


def test_no_class_anywhere_is_refused() -> None:
    with pytest.raises(StructureDefinitionError, match="does not say what it returns"):

        @structure("@things/thing")
        async def expand_thing(id: str, things: ThingClient):  # noqa: ANN202 -- what this test is about: an expander that does not say what it returns
            return await things.aget_thing(id)


def test_a_non_class_annotation_needs_cls() -> None:
    with pytest.raises(StructureDefinitionError, match="is not a class"):

        @structure("@things/thing")
        async def expand_thing(id: str, things: ThingClient) -> Thing | None:
            return await things.aget_thing(id)


def test_class_and_annotation_must_agree() -> None:
    with pytest.raises(StructureDefinitionError, match="One structure, one class"):

        @structure("@things/thing", cls=Other)
        async def expand_thing(id: str, things: ThingClient) -> Thing:
            return await things.aget_thing(id)
