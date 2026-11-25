"""An app is assembled from what its annotations reach, when it is snapshotted."""

from typing import Self

import pytest

from arkitekt_spec.declare.assemble import assemble
from arkitekt_spec.declare.bundle import Bundle
from arkitekt_spec.declare.client import client
from arkitekt_spec.declare.errors import AppDeclarationError
from arkitekt_spec.declare.stamps import model, state
from arkitekt_spec.declare.structures.convert import AppNamespace
from arkitekt_spec.declare.structures.declare import structure


@client(requires={})
class Pictures:
    @classmethod
    def build(cls) -> Self:
        return cls()


class Picture:
    def __init__(self, id: str) -> None:
        self.id = id


class Thumbnail:
    def __init__(self, id: str) -> None:
        self.id = id


@structure("@pictures/picture")
async def expand_picture(id: str, pictures: Pictures) -> Picture:
    return Picture(id)


@structure("@pictures/thumbnail")
async def expand_thumbnail(id: str, pictures: Pictures) -> Thumbnail:
    return Thumbnail(id)


@model
class Album:
    pictures: list[Picture]


@state(persist=None)
class Counter:
    value: int = 0


def test_a_structure_in_a_model_field_brings_its_client() -> None:
    app = Bundle()

    def show(album: Album) -> int:
        return len(album.pictures)

    app.register_action(show)
    registry = assemble(app)

    assert list(registry.clients) == ["pictures"]
    # Only what the annotations reach: Thumbnail is named nowhere.
    assert set(registry.structure_registry.identifier_structure_map) == {
        "@pictures/picture"
    }
    picture = registry.structure_registry.get_fullfilled_structure("@pictures/picture")
    assert picture.client == "pictures"


def test_one_model_gets_each_apps_own_identifier() -> None:
    def make(name: str) -> Bundle:
        app = Bundle()

        def show(album: Album) -> int:
            return 0

        app.register_action(show)
        return app

    one = assemble(
        make("one"), namespace=AppNamespace(identifier="one", module=__name__)
    )
    two = assemble(
        make("two"), namespace=AppNamespace(identifier="two", module=__name__)
    )

    assert "@one/album" in one.structure_registry.identifier_model_map
    assert "@two/album" in two.structure_registry.identifier_model_map


def test_a_per_app_declaration_wins_over_the_mark() -> None:
    app = Bundle()
    app._record("state", Counter, persist="synchronous")

    def count(counter: Counter) -> int:
        return counter.value

    app.register_action(count)
    registry = assemble(app)

    assert registry.structure_registry.states[Counter].persist == "synchronous"


def test_every_problem_is_reported_together_with_its_line() -> None:
    class Unknown:
        pass

    app = Bundle()

    def one(x: Unknown) -> int:
        return 1

    def two(x: Unknown) -> int:
        return 2

    app.register_action(one)
    app.register_action(two)

    with pytest.raises(AppDeclarationError) as raised:
        assemble(app)

    assert len(raised.value.exceptions) == 2
    assert all(
        __file__ in " ".join(getattr(e, "__notes__", []))
        for e in raised.value.exceptions
    )
